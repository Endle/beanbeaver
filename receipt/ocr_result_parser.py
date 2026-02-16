"""Parse raw OCR text into structured Receipt data."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from beanbeaver.domain.receipt import Receipt, ReceiptItem, ReceiptWarning

from .date_utils import placeholder_receipt_date
from .item_categories import categorize_item

# Minimum average confidence for a line to be considered reliable
MIN_LINE_CONFIDENCE = 0.6


# Bbox-based parsing constants
MIN_CONFIDENCE = 0.5  # Ignore words with lower OCR confidence
PRICE_X_THRESHOLD = 0.65  # Prices typically appear on right side (X > this)
ITEM_X_THRESHOLD = 0.6  # Item names typically appear on left side (X < this)
Y_TOLERANCE = 0.02  # How close Y coordinates must be to be "same row"
MAX_ITEM_DISTANCE = 0.08  # Max vertical distance to associate price with item

# Section headers to skip (not actual items)
SECTION_HEADERS = {"MEAT", "SEAFOOD", "PRODUCE", "DELI", "GROCERY", "BAKERY", "FROZEN"}
SECTION_HEADER_WITH_AISLE = re.compile(r"^\d{1,2}\s*[-:]\s*[A-Z]{3,}$")
SECTION_AISLE_PREFIX = re.compile(r"^\d{1,2}\s*[-:]")

# Summary line patterns to exclude
SUMMARY_PATTERNS = re.compile(
    r"^(SUB\s*TOTAL|SUBTOTAL|TOTAL|HST|GST|PST|TAX|MASTER|VISA|DEBIT|"
    r"CREDIT|POINTS|CASH|CHANGE|BALANCE|APPROVED|CARD|TERMINAL|MEMBER)",
    re.IGNORECASE,
)

# Footer/address-like lines to skip as items
FOOTER_ADDRESS_PATTERNS = re.compile(
    r"\b(AVE|AVENUE|ST|STREET|RD|ROAD|BLVD|BOULEVARD|DR|DRIVE|HWY|HIGHWAY)\b|"
    r"\b(MARKHAM|TORONTO|MISSISSAUGA|RICHMOND\s+HILL|ON|ONTARIO)\b|"
    r"\b(L\d[A-Z]\d)\b|"
    r"\(\d{3}\)\s*\d{3}-\d{4}",
    re.IGNORECASE,
)

# Quantity/weight modifier patterns for multi-row item formats
# These patterns detect lines like "3 @ $1.99", "1.22 lb @ $2.99/lb", "2 /for $3.00"
QUANTITY_MODIFIER_PATTERNS = [
    # "3 @ $1.99" - count at unit price
    (re.compile(r"^(\d+)\s*@\s*\$?(\d+\.\d{2})"), "count_at_price"),
    # "1.22 lb @ $2.99/lb" or "1.22 lk @ $2.99/1b" (OCR errors: lk=lb, k9/kg=kg, 1b=lb)
    (re.compile(r"^(\d+\.?\d*)\s*(?:lb|lk|kg|k[g9]|1b|1k)\s*@", re.IGNORECASE), "weight_at_price"),
    # "2 /for $3.00" or "(2 /for $3.00)"
    (re.compile(r"^\(?(\d+)\s*/\s*for\s+\$?(\d+\.\d{2})\)?"), "multi_for_price"),
]


def _is_section_header_text(text: str) -> bool:
    """Return True if text looks like a section/aisle header, not an item."""
    if not text:
        return False
    normalized = re.sub(r"\s+", " ", text.strip().upper())
    if normalized in SECTION_HEADERS:
        return True
    # Handles headers like "21-GROCERY", "22-DAIRY", "31-MEATS", including OCR variants.
    if SECTION_HEADER_WITH_AISLE.match(normalized):
        return True
    # Handles aisle-prefixed variants with suffix words, e.g. "33-BAKERY INSTORE".
    if SECTION_AISLE_PREFIX.match(normalized):
        tokens = set(re.findall(r"[A-Z]+", normalized))
        if tokens & SECTION_HEADERS:
            return True
    return False


def _strip_leading_receipt_codes(text: str) -> str:
    """Remove leading quantity/SKU prefixes from an OCR item line."""
    if not text:
        return text
    cleaned = text.strip()
    # Optional quantity prefix like "(2)" often precedes SKU on grocery receipts.
    cleaned = re.sub(r"^\(\d+\)\s*", "", cleaned)
    # Remove long leading SKU codes.
    cleaned = re.sub(r"^\d{6,}\s*", "", cleaned)
    return cleaned.strip()


def _looks_like_summary_line(text: str) -> bool:
    """Return True if text appears to be a summary/tax/payment line."""
    if not text:
        return False
    upper = text.upper().strip()
    if SUMMARY_PATTERNS.match(upper):
        return True
    if "SUBTOTAL" in upper or "SUB TOTAL" in upper:
        return True
    if "TOTAL" in upper:
        return True
    if re.search(r"\b(HST|GST|PST|TAX)\b", upper):
        return True
    # Handles variants like "H=HST 13% 2.19"
    if upper.startswith("H=") and any(tag in upper for tag in ("HST", "GST", "PST", "TAX")):
        return True
    return False


def _line_has_trailing_price(text: str) -> bool:
    """Return True if the line itself ends with a price."""
    if not text:
        return False
    return re.search(r"\d+\.\d{2}\s*[HhTt]?\s*$", text.strip()) is not None


GENERIC_PRICED_ITEM_LABELS = {"MEAT"}


def _is_priced_generic_item_label(left_text: str, full_text: str) -> bool:
    """Allow short generic labels when they clearly carry an item price."""
    if not left_text:
        return False
    return _line_has_trailing_price(full_text) and left_text.strip().upper() in GENERIC_PRICED_ITEM_LABELS


def _parse_quantity_modifier(line: str) -> dict | None:
    """
    Parse quantity/weight modifier from a line.

    Detects patterns like:
    - "3 @ $1.99" (count at unit price)
    - "1.22 lb @ $2.99/lb" (weight at unit price)
    - "2 /for $3.00" (multi-buy deal)

    Args:
        line: Text line to parse

    Returns:
        dict with keys: quantity, unit_price (optional), weight (optional),
        pattern_type, raw_line; or None if not a modifier line
    """
    line = line.strip()

    for pattern, pattern_type in QUANTITY_MODIFIER_PATTERNS:
        match = pattern.match(line)
        if match:
            groups = match.groups()
            if pattern_type == "count_at_price":
                return {
                    "quantity": int(groups[0]),
                    "unit_price": Decimal(groups[1]),
                    "pattern_type": pattern_type,
                    "raw_line": line,
                }
            elif pattern_type == "weight_at_price":
                return {
                    "quantity": 1,  # Weight items are qty=1
                    "weight": Decimal(groups[0]),
                    "pattern_type": pattern_type,
                    "raw_line": line,
                }
            elif pattern_type == "multi_for_price":
                qty = int(groups[0])
                total = Decimal(groups[1])
                return {
                    "quantity": qty,
                    "unit_price": total / qty,
                    "deal_price": total,  # The "X for $Y" total
                    "pattern_type": pattern_type,
                    "raw_line": line,
                }
    return None


def _validate_quantity_price(total_price: Decimal, modifier: dict, tolerance: Decimal = Decimal("0.02")) -> bool:
    """
    Validate that quantity × unit_price ≈ total_price.

    This helps confirm we matched the right modifier to the right price,
    preventing cascade errors where modifiers get paired with wrong totals.

    Args:
        total_price: The total price from the receipt
        modifier: Parsed modifier dict from _parse_quantity_modifier()
        tolerance: Maximum allowed difference (default $0.02)

    Returns:
        True if the modifier validates against the total price
    """
    if modifier is None:
        return False

    pattern_type = modifier.get("pattern_type")

    if pattern_type == "count_at_price":
        expected = modifier["quantity"] * modifier["unit_price"]
        return abs(expected - total_price) <= tolerance

    elif pattern_type == "multi_for_price":
        # For "2 /for $3.00", the deal_price should equal total_price
        return abs(modifier["deal_price"] - total_price) <= tolerance

    elif pattern_type == "weight_at_price":
        # Weight items can't be validated without knowing the unit price
        # Just accept them as valid modifiers
        return True

    return False


def _looks_like_quantity_expression(text: str) -> bool:
    """
    Return True if text is a quantity/offer modifier line, not an item description.

    This intentionally avoids broad slash-based matching so product names like
    "50/70 SHRIMP" are not misclassified as quantity lines.
    """
    text = text.strip()
    if not text:
        return False

    # Structured patterns handled by _parse_quantity_modifier()
    if _parse_quantity_modifier(text):
        return True

    # Additional quantity/offer formats seen in receipts
    return bool(
        re.match(r"^\d+\s*/\s*for\b", text, re.IGNORECASE)
        or re.match(r"^\d+\s*@\s*\d+\s*/\s*\$?\d+\.\d{2}\b", text, re.IGNORECASE)
        or re.match(r"^\(\d+\s*/\s*for\s+\$[\d.]+\)", text)
        or re.match(r"^\([^)]+\)\s+\d+\s*/\s*for\b", text, re.IGNORECASE)
    )


def _get_word_y_center(word: dict[str, Any]) -> float:
    """Get the vertical center of a word from its bbox."""
    bbox = word.get("bbox", [[0, 0], [0, 0]])
    y_top = bbox[0][1]
    y_bottom = bbox[1][1]
    return (y_top + y_bottom) / 2


def _get_word_x_center(word: dict[str, Any]) -> float:
    """Get the horizontal center of a word from its bbox."""
    bbox = word.get("bbox", [[0, 0], [0, 0]])
    x_left = bbox[0][0]
    x_right = bbox[1][0]
    return (x_left + x_right) / 2


def _is_price_word(word: dict[str, Any]) -> Decimal | None:
    """Check if a word is a price pattern. Returns the price or None."""
    text = word.get("text", "")
    # Normalize common prefixes like "W $18.99" used by some receipts (e.g., T&T)
    text = text.strip()
    text = re.sub(r"^[Ww]\s*", "", text)
    # Match $X.XX or X.XX patterns
    match = re.match(r"^\$?(\d+\.\d{2})$", text)
    if match:
        try:
            return Decimal(match.group(1))
        except InvalidOperation:
            return None
    return None


def _extract_items_with_bbox(
    pages: list[dict[str, Any]],
    warning_sink: list[ReceiptWarning] | None = None,
) -> list[ReceiptItem]:
    """
    Extract items using bounding box spatial data.

    This handles receipts where items and prices are on the same row
    but at opposite ends (e.g., T&T Supermarket format).

    Strategy:
    1. Find all price words on the right side of the receipt
    2. For each price, find item description words on the same Y-coordinate
    3. If no item on same row, look at lines above the price
    4. Filter out section headers and summary lines
    """
    items: list[ReceiptItem] = []

    if not pages:
        return items

    # Collect all words with their positions and confidence
    all_words: list[dict[str, Any]] = []
    # Map each word object to its source line context.
    word_to_line: dict[int, tuple[float, str, str]] = {}
    for page in pages:
        for line in page.get("lines", []):
            for word in line.get("words", []):
                confidence = word.get("confidence", 0)
                if confidence >= MIN_CONFIDENCE:
                    all_words.append(word)

    # Collect lines with their Y positions and left-side text (for item matching)
    # Each entry: (line_y, full_text, left_side_text, left_x)
    all_lines: list[tuple[float, str, str, float]] = []
    for page in pages:
        for line in page.get("lines", []):
            if not line.get("words"):
                continue
            full_text = line.get("text", "")
            line_has_price = _line_has_trailing_price(full_text)
            # Extract left-side words (X < ITEM_X_THRESHOLD) for item description
            # Track Y of first valid left-side word (not filtered-out section headers)
            left_words = []
            left_x = 1.0  # Track leftmost X position
            left_y = None  # Track Y of first valid left-side word
            for word in line.get("words", []):
                x_center = _get_word_x_center(word)
                if x_center < ITEM_X_THRESHOLD:
                    text = word.get("text", "")
                    # Skip unwanted patterns
                    if len(text) <= 1 or re.match(r"^[\d.]+$", text):
                        continue
                    if _is_section_header_text(text) and not line_has_price:
                        continue
                    left_words.append(text)
                    left_x = min(left_x, x_center)
                    if left_y is None:
                        left_y = _get_word_y_center(word)
            left_text = " ".join(left_words)
            # Use Y of first valid word, or fall back to first word of line
            line_y = left_y if left_y is not None else _get_word_y_center(line["words"][0])
            all_lines.append((line_y, full_text, left_text, left_x))
            for word in line.get("words", []):
                word_to_line[id(word)] = (line_y, full_text, left_text)

    # Find the Y-position of the TOTAL line to avoid footer/address section
    total_line_y = None
    for line_y, full_text, _, _ in all_lines:
        full_upper = full_text.upper()
        if "TOTAL" in full_upper and "SUBTOTAL" not in full_upper:
            total_line_y = line_y if total_line_y is None else min(total_line_y, line_y)

    # Find price words on the right side (exclude $0.00)
    price_words = []
    for word in all_words:
        x_center = _get_word_x_center(word)
        price = _is_price_word(word)
        if price is not None and price > Decimal("0.00") and x_center > PRICE_X_THRESHOLD:
            price_words.append((word, price))

    # Track which item lines have been used (by Y position) to prevent reuse
    used_item_y_positions: set[float] = set()

    # For each price, find associated item description
    for price_word, price in price_words:
        found_item = False
        price_y = _get_word_y_center(price_word)
        # Ignore prices in payment/footer section below TOTAL.
        if total_line_y is not None and price_y > total_line_y + Y_TOLERANCE:
            continue
        # Find the line closest to this price (to detect header+price rows)
        closest_line_to_price = min(all_lines, key=lambda line_entry: abs(line_entry[0] - price_y), default=None)
        prefer_below = False
        price_line_has_onsale = False
        onsale_target_line = None
        source_line_y = None
        source_full_text = ""
        source_left_text = ""
        source_line_ctx = word_to_line.get(id(price_word))
        if source_line_ctx:
            source_line_y, source_full_text, source_left_text = source_line_ctx
        if closest_line_to_price:
            line_y, full_text, left_text, _ = closest_line_to_price
            full_upper = source_full_text.upper() if source_full_text else full_text.upper()
            price_line_has_onsale = ("ONSALE" in full_upper) or ("ON SALE" in full_upper)
            left_is_header = _is_section_header_text(left_text) and not _is_priced_generic_item_label(
                left_text, full_text
            )
            if left_is_header or _is_section_header_text(full_text) or not left_text:
                prefer_below = True
            # ONSALE marker rows usually carry sale price for adjacent item text.
            if price_line_has_onsale:
                prefer_below = True

        # Skip if this price belongs to a summary/payment line.
        # Use line-level context instead of broad Y-band word matching so nearby
        # lines (e.g., MEMBER PRICING above produce items) don't suppress items.
        is_summary = False

        def is_valid_onsale_target(full_text: str, left_text: str) -> bool:
            if not left_text:
                return False
            if _looks_like_summary_line(left_text) or _looks_like_summary_line(full_text):
                return False
            if _is_section_header_text(left_text) or _is_section_header_text(full_text):
                return False
            if _looks_like_quantity_expression(left_text):
                return False
            if _line_has_trailing_price(full_text):
                return False
            stripped = _strip_leading_receipt_codes(left_text)
            if not stripped:
                return False
            alpha_count = sum(1 for c in stripped if c.isalpha())
            if alpha_count / len(stripped) < 0.5:
                return False
            return True

        if total_line_y is not None and price_y > total_line_y - MAX_ITEM_DISTANCE:
            for candidate_y, candidate_full_text, candidate_left_text, _ in all_lines:
                if abs(candidate_y - price_y) > Y_TOLERANCE:
                    continue
                if _looks_like_summary_line(candidate_left_text) or _looks_like_summary_line(candidate_full_text):
                    is_summary = True
                    break
        if closest_line_to_price:
            line_y, full_text, left_text, _ = closest_line_to_price
            full_text_stripped = full_text.strip()
            if _looks_like_summary_line(left_text) or _looks_like_summary_line(full_text):
                is_summary = True
            elif re.match(r"^\$?\d+\.\d{2}\s*$", full_text_stripped):
                # Two-line summaries like:
                #   TOTAL
                #   73.63
                # The amount line itself has no summary keyword, so inspect nearest
                # preceding line only.
                nearest_above = None
                for candidate in all_lines:
                    if candidate[0] >= line_y:
                        continue
                    if nearest_above is None or candidate[0] > nearest_above[0]:
                        nearest_above = candidate
                if nearest_above:
                    above_y, above_full_text, above_left_text, _ = nearest_above
                    if line_y - above_y <= MAX_ITEM_DISTANCE and (
                        _looks_like_summary_line(above_left_text) or _looks_like_summary_line(above_full_text)
                    ):
                        is_summary = True
                # In dense summary blocks, labels can appear slightly above/below
                # the amount due to OCR row grouping jitter. If this standalone
                # price is near the TOTAL section, treat neighboring summary labels
                # as authoritative.
                if not is_summary and total_line_y is not None and line_y > total_line_y - MAX_ITEM_DISTANCE:
                    for candidate_y, candidate_full_text, candidate_left_text, _ in all_lines:
                        if abs(candidate_y - line_y) > MAX_ITEM_DISTANCE:
                            continue
                        if _looks_like_summary_line(candidate_left_text) or _looks_like_summary_line(
                            candidate_full_text
                        ):
                            is_summary = True
                            break
            # ONSALE-only rows can be promo metadata. Keep them only when the
            # nearest valid item below looks like a promoted item marker row.
            if not is_summary and price_line_has_onsale:
                anchor_y = source_line_y if source_line_y is not None else line_y
                nearest_below = None
                for candidate_y, candidate_full_text, candidate_left_text, candidate_left_x in all_lines:
                    if candidate_y <= anchor_y:
                        continue
                    if candidate_y - anchor_y > MAX_ITEM_DISTANCE:
                        continue
                    if not is_valid_onsale_target(candidate_full_text, candidate_left_text):
                        continue
                    if nearest_below is None or candidate_y < nearest_below[0]:
                        nearest_below = (candidate_y, candidate_full_text, candidate_left_text, candidate_left_x)
                if nearest_below:
                    onsale_target_line = nearest_below
                else:
                    is_summary = True

        if is_summary:
            continue

        # Find the closest line to this price that has left-side item text
        # First pass: look for items strictly above or at the price level
        # If we detected a header+price row, prefer matching the next valid item below
        # Second pass: if nothing found, allow small tolerance below for same-row items
        closest_line = None
        closest_distance = float("inf")

        def is_valid_item_line(line_y: float, left_text: str, full_text: str) -> bool:
            """Check if a line is a valid item description."""
            if not left_text:
                return False
            if len(left_text) < 5 and not _is_priced_generic_item_label(left_text, full_text):
                return False
            if total_line_y is not None and line_y > total_line_y + Y_TOLERANCE:
                return False
            if _looks_like_summary_line(left_text) or _looks_like_summary_line(full_text):
                return False
            left_is_header = _is_section_header_text(left_text) and not _is_priced_generic_item_label(
                left_text, full_text
            )
            if left_is_header or _is_section_header_text(full_text):
                return False
            # Skip bare item/SKU code lines, but allow SKU-prefixed item descriptions.
            if re.match(r"^\d{8,}\s*$", full_text):
                return False
            left_text_for_ratio = _strip_leading_receipt_codes(left_text)
            if not left_text_for_ratio:
                return False
            alpha_count = sum(1 for c in left_text_for_ratio if c.isalpha())
            if alpha_count / len(left_text_for_ratio) < 0.5:
                return False
            # Skip common OCR garbage patterns (garbled Chinese text)
            if re.match(r"^\(H{1,2}E[DI]?\b", left_text):
                return False
            # Skip short single-word garbage (likely failed OCR)
            # Valid items usually have multiple words or are longer
            if len(left_text) < 8 and " " not in left_text and not _is_priced_generic_item_label(left_text, full_text):
                return False
            if FOOTER_ADDRESS_PATTERNS.search(full_text):
                return False
            # Skip promotional/sale lines like "(#)<ON SALE)", "(KAE)<ON SALE)"
            if re.search(r"ON\s*SALE", left_text, re.IGNORECASE):
                return False
            # Skip quantity expressions like "(1 /for $2.99)", "(2 /for $4.50)"
            if re.match(r"^\(\d+\s*/\s*for\s+\$[\d.]+\)", left_text):
                return False
            # Skip lines that are mostly parenthetical codes
            if re.match(r"^\([^)]{1,5}\)", left_text) and len(left_text) < 12:
                return False
            return True

        # Fast path: use the nearest line directly only when it is clearly a
        # descriptive priced item row (not a qty/offer expression row).
        if closest_line_to_price:
            line_y, full_text, left_text, left_x = closest_line_to_price
            if (
                line_y not in used_item_y_positions
                and abs(line_y - price_y) <= Y_TOLERANCE
                and _line_has_trailing_price(full_text)
                and not _looks_like_quantity_expression(left_text)
                and is_valid_item_line(line_y, left_text, full_text)
            ):
                closest_line = (line_y, full_text, left_text, left_x)
                closest_distance = abs(line_y - price_y)

        if onsale_target_line and onsale_target_line[0] not in used_item_y_positions:
            closest_line = onsale_target_line
            closest_distance = abs(onsale_target_line[0] - price_y)

        if prefer_below and closest_line is None:
            # Prefer the nearest valid item below when price is on a header line
            for line_y, full_text, left_text, left_x in all_lines:
                if line_y < price_y:
                    continue
                if line_y - price_y > MAX_ITEM_DISTANCE:
                    continue
                if not is_valid_item_line(line_y, left_text, full_text):
                    continue
                if line_y in used_item_y_positions:
                    continue
                distance = abs(line_y - price_y)
                if distance < closest_distance:
                    closest_distance = distance
                    closest_line = (line_y, full_text, left_text, left_x)
        # First pass: only items at or above price
        if closest_line is None:
            for line_y, full_text, left_text, left_x in all_lines:
                if line_y > price_y:  # Strictly above (smaller Y = higher on page)
                    continue
                if price_y - line_y > MAX_ITEM_DISTANCE:
                    continue
                # For ONSALE rows, avoid attaching to previous lines that already
                # have their own explicit price.
                if price_line_has_onsale and line_y < price_y and _line_has_trailing_price(full_text):
                    continue
                if not is_valid_item_line(line_y, left_text, full_text):
                    continue
                # Skip items already used by another price
                if line_y in used_item_y_positions:
                    continue
                distance = abs(line_y - price_y)
                if distance < closest_distance:
                    closest_distance = distance
                    closest_line = (line_y, full_text, left_text, left_x)

        # Second pass: ONLY if nothing found above, allow same-row tolerance below
        # Use larger tolerance (2x) for items that appear on the same visual row
        if closest_line is None:
            for line_y, full_text, left_text, left_x in all_lines:
                # Allow slightly below (same row due to word height variations)
                if line_y > price_y + Y_TOLERANCE * 2:
                    continue
                if line_y <= price_y:  # Already checked in first pass
                    continue
                if not is_valid_item_line(line_y, left_text, full_text):
                    continue
                # Skip items already used by another price
                if line_y in used_item_y_positions:
                    continue
                distance = abs(line_y - price_y)
                if distance < closest_distance:
                    closest_distance = distance
                    closest_line = (line_y, full_text, left_text, left_x)

        if closest_line and closest_distance <= Y_TOLERANCE:
            line_y, _, left_text, _ = closest_line
            # Clean up the description
            description = _clean_description(left_text)

            if description and len(description) > 2:
                # Mark this item line as used
                used_item_y_positions.add(line_y)
                items.append(
                    ReceiptItem(
                        description=description,
                        price=price,
                        category=categorize_item(description),
                    )
                )
                found_item = True
        else:
            # No item on same row - look backwards at lines above this price
            # Find lines with Y < price_y, sorted by Y descending (closest first)
            lines_above = [
                (y, full, left, x)
                for y, full, left, x in all_lines
                if y < price_y - Y_TOLERANCE and (price_y - y) <= MAX_ITEM_DISTANCE
            ]
            lines_above.sort(key=lambda x: x[0], reverse=True)

            for line_y, full_text, left_text, _ in lines_above[:5]:  # Check up to 5 lines above
                # Skip items already used by another price
                if line_y in used_item_y_positions:
                    continue
                if price_line_has_onsale and _line_has_trailing_price(full_text):
                    continue
                # Skip empty lines, summary lines, weight info, prices
                if not left_text or len(left_text) < 3:
                    continue
                if _looks_like_summary_line(left_text) or _looks_like_summary_line(full_text):
                    continue
                if re.match(r"^\d+\.\d+\s*kg", full_text, re.IGNORECASE):
                    continue
                if re.match(r"^W\s*\$", full_text):
                    continue
                if re.match(r"^\$?\d+\.\d{2}$", full_text):
                    continue
                left_is_header = _is_section_header_text(left_text) and not _is_priced_generic_item_label(
                    left_text, full_text
                )
                if left_is_header or _is_section_header_text(full_text):
                    continue
                # Skip garbled OCR lines (mostly non-alpha)
                left_text_for_ratio = _strip_leading_receipt_codes(left_text)
                if not left_text_for_ratio:
                    continue
                alpha_count = sum(1 for c in left_text_for_ratio if c.isalpha())
                if alpha_count < len(left_text_for_ratio) * 0.4:
                    continue

                description = _clean_description(left_text)
                if description and len(description) > 2:
                    # Mark this item line as used
                    used_item_y_positions.add(line_y)
                    items.append(
                        ReceiptItem(
                            description=description,
                            price=price,
                            category=categorize_item(description),
                        )
                    )
                    found_item = True
                    break

        if not found_item and warning_sink is not None:
            context_text = source_full_text.strip() if source_full_text else ""
            if not context_text and closest_line_to_price:
                context_text = closest_line_to_price[1].strip()
            context_text = context_text[:80] if context_text else ""
            message = f"maybe missed item near price {price:.2f}"
            if context_text:
                message += f' (context: "{context_text}")'
            warning_sink.append(
                ReceiptWarning(
                    message=message,
                    after_item_index=(len(items) - 1) if items else None,
                )
            )

    # Keep duplicates: repeated items with identical descriptions/prices are valid.
    return items


def _clean_description(desc: str) -> str:
    """Clean up item description from OCR artifacts."""
    # Remove leading quantity prefix like "(2)" and then long SKU.
    desc = re.sub(r"^\(\d+\)\s*", "", desc)
    # Remove common OCR artifacts and sale markers
    desc = re.sub(r"\(SALE\)\s*", "", desc, flags=re.IGNORECASE)
    desc = re.sub(r"\(HED[^)]*\)\s*", "", desc, flags=re.IGNORECASE)
    desc = re.sub(r"\(HHED[^)]*\)\s*", "", desc, flags=re.IGNORECASE)
    # Remove quantity patterns like "@2/S2.97", "38/52.97", "02/54.47"
    desc = re.sub(r"@?\d+/[A-Za-z]?\$?\d+\.\d{2}", "", desc)
    desc = re.sub(r"\d+/\$?\d+\.\d{2}", "", desc)
    # Remove price-per-unit patterns like "$8.80/K9", "$5.03/k3"
    desc = re.sub(r"\$\d+\.\d+/\w+", "", desc)
    # Remove standalone price patterns that might have slipped through
    desc = re.sub(r"\$\d+\.\d{2}", "", desc)
    # Remove garbled code patterns like "0s0.99ea"
    desc = re.sub(r"\d+s\d+\.\d+ea", "", desc, flags=re.IGNORECASE)
    # Remove SKU-like patterns (6+ digits at start)
    desc = re.sub(r"^\d{6,}\s*", "", desc)
    # Remove common garbled OCR words
    desc = re.sub(r"\bCAHRD\b", "", desc, flags=re.IGNORECASE)
    desc = re.sub(r"\bHED\b", "", desc, flags=re.IGNORECASE)
    # Remove leading/trailing special chars and extra spaces
    desc = re.sub(r"^[^A-Za-z0-9]+", "", desc)
    desc = re.sub(r"[^A-Za-z0-9)]+$", "", desc)
    desc = re.sub(r"\s+", " ", desc)
    return desc.strip()


def _has_useful_bbox_data(pages: list[dict[str, Any]]) -> bool:
    """Check if the OCR result has useful bbox data for spatial parsing."""
    if not pages:
        return False

    # Check first page for bbox data
    for line in pages[0].get("lines", [])[:10]:
        for word in line.get("words", []):
            if "bbox" in word and len(word["bbox"]) >= 2:
                return True
    return False


def _is_spatial_layout_receipt(pages: list[dict[str, Any]], full_text: str) -> bool:
    """
    Detect if this receipt has a spatial layout where items and prices
    are on opposite sides of the same row (requiring bbox-based parsing).

    Examples: T&T, Real Canadian Superstore, and similar formats.
    """
    full_text_upper = full_text.upper()

    # Check for known merchants with this layout
    spatial_merchants = [
        "T&T",
        "T & T",
        "REAL CANADIAN",
        "SUPERSTORE",
        "C&C",
        "C & C",
    ]
    for merchant in spatial_merchants:
        if merchant in full_text_upper:
            return True

    # Check for "W $" pattern which is characteristic of T&T
    w_price_pattern = re.compile(r"W\s+\$\d+\.\d{2}")
    if w_price_pattern.search(full_text):
        return True

    return False


def parse_receipt(
    ocr_result: dict,
    image_filename: str = "",
    known_merchants: list[str] | tuple[str, ...] | None = None,
) -> Receipt:
    """
    Parse OCR result into a Receipt object.

    This is a best-effort parser - results should be manually reviewed.

    Args:
        ocr_result: JSON response from OCR service with 'full_text' and 'pages'
        image_filename: Source image filename for reference
        known_merchants: Optional merchant keywords loaded by runtime components.

    Returns:
        Receipt object with parsed data
    """
    full_text = ocr_result.get("full_text", "")
    pages = ocr_result.get("pages", [])
    lines = [line.strip() for line in full_text.split("\n") if line.strip()]

    merchant = _extract_merchant(lines, full_text, pages, known_merchants=known_merchants)
    receipt_date = _extract_date(lines, full_text)
    date_is_placeholder = False
    if receipt_date is None:
        receipt_date = placeholder_receipt_date()
        date_is_placeholder = True
    total = _extract_total(lines)
    tax = _extract_tax(lines)
    subtotal = _extract_subtotal(lines)

    # Collect known summary amounts to filter from items
    summary_amounts = set()
    if total:
        summary_amounts.add(total)
    if tax:
        summary_amounts.add(tax)
    if subtotal:
        summary_amounts.add(subtotal)

    # Try bbox-based spatial parsing for receipts with items and prices on same row
    items: list[ReceiptItem] = []
    warnings: list[ReceiptWarning] = []
    if _has_useful_bbox_data(pages) and _is_spatial_layout_receipt(pages, full_text):
        items = _extract_items_with_bbox(pages, warning_sink=warnings)

    # Fall back to text-based parsing if bbox parsing didn't find items
    if not items:
        items = _extract_items(lines, summary_amounts, warning_sink=warnings)

    return Receipt(
        merchant=merchant,
        date=receipt_date,
        date_is_placeholder=date_is_placeholder,
        total=total,
        items=items,
        tax=tax,
        subtotal=subtotal,
        raw_text=full_text,
        image_filename=image_filename,
        warnings=warnings,
    )


def _extract_merchant(
    lines: list[str],
    full_text: str = "",
    pages: list[dict[str, Any]] | None = None,
    known_merchants: list[str] | tuple[str, ...] | None = None,
) -> str:
    """
    Extract merchant name using multiple strategies.

    Strategy order:
    1. Search for runtime-provided known merchants in full text
    2. Use confidence-weighted extraction from pages data (skip low-confidence lines)
    3. Fall back to first meaningful line (original behavior)
    """
    # Strategy 1: Search for known merchants in full text
    known_merchants = known_merchants or []
    full_text_upper = full_text.upper()

    # Sort by length descending to match longer/more specific names first
    # Use word boundary matching to avoid matching substrings (e.g., "DOORDASH" in "DOORDASH2X50")
    for merchant in sorted(known_merchants, key=len, reverse=True):
        pattern = r"\b" + re.escape(merchant.upper()) + r"\b"
        if re.search(pattern, full_text_upper):
            return merchant

    # Strategy 2: Use pages data with confidence scores
    if pages:
        confident_merchant = _extract_merchant_with_confidence(pages)
        if confident_merchant:
            return confident_merchant

    # Strategy 3: Fall back to first meaningful line (original behavior)
    for line in lines[:5]:
        # Skip lines that look like dates, numbers only, or very short
        if len(line) > 3 and not re.match(r"^[\d/\-:]+$", line):
            # Clean up common OCR artifacts
            cleaned = re.sub(r"[^\w\s&\'-]", "", line).strip()
            if len(cleaned) > 2:
                return cleaned

    return "UNKNOWN_MERCHANT"


def _extract_merchant_with_confidence(pages: list[dict[str, Any]]) -> str | None:
    """
    Extract merchant name using OCR confidence scores.

    Looks at the first few lines and picks the first one with
    high average word confidence.
    """
    if not pages:
        return None

    # Check first 10 lines for a high-confidence merchant name
    lines_checked = 0
    for page in pages:
        for line in page.get("lines", []):
            if lines_checked >= 10:
                break

            words = line.get("words", [])
            if not words:
                continue

            # Calculate average confidence for this line
            confidences = [w.get("confidence", 0) for w in words]
            avg_confidence = sum(confidences) / len(confidences)

            # Skip low-confidence lines (likely garbled OCR)
            if avg_confidence < MIN_LINE_CONFIDENCE:
                lines_checked += 1
                continue

            line_text = line.get("text", "").strip()

            # Skip lines that look like dates, numbers only, or very short
            if len(line_text) <= 3:
                lines_checked += 1
                continue
            if re.match(r"^[\d/\-:]+$", line_text):
                lines_checked += 1
                continue

            # Clean up common OCR artifacts
            cleaned = re.sub(r"[^\w\s&\'-]", "", line_text).strip()
            if len(cleaned) > 2:
                return cleaned

            lines_checked += 1

    return None


def _extract_date(lines: list[str], full_text: str) -> date | None:
    """Extract date from receipt (returns None if unknown)."""
    # Common date patterns
    patterns = [
        # MM/DD/YY or DD/MM/YY
        r"(\d{1,2})[/-](\d{1,2})[/-](\d{2})",
        # MM/DD/YYYY or DD/MM/YYYY
        r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})",
        # YYYY-MM-DD or YYYY/MM/DD or YYYY.MM.DD
        r"(\d{4})[./-](\d{2})[./-](\d{2})",
        # YYYYMMDD
        r"\b(\d{4})(\d{2})(\d{2})\b",
        # Month DD, YYYY
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+(\d{1,2}),?\s+(\d{4})",
    ]

    for pattern in patterns:
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            try:
                groups = match.groups()
                if len(groups) == 3:
                    if groups[0].isalpha():
                        # Month name format
                        month_map = {
                            "jan": 1,
                            "feb": 2,
                            "mar": 3,
                            "apr": 4,
                            "may": 5,
                            "jun": 6,
                            "jul": 7,
                            "aug": 8,
                            "sep": 9,
                            "oct": 10,
                            "nov": 11,
                            "dec": 12,
                        }
                        month = month_map.get(groups[0][:3].lower(), 1)
                        day = int(groups[1])
                        year = int(groups[2])
                    elif len(groups[0]) == 4:
                        # YYYY-MM-DD
                        year = int(groups[0])
                        month = int(groups[1])
                        day = int(groups[2])
                    else:
                        # MM/DD/YY or MM/DD/YYYY - assume North America
                        month = int(groups[0])
                        day = int(groups[1])
                        year = int(groups[2])
                        if year < 100:
                            # Map 2-digit years to 2000s/1900s
                            year = 2000 + year if year <= 69 else 1900 + year

                    return date(year, month, day)
            except (ValueError, KeyError):
                continue

    # Leave unknown if no date found
    return None


def _extract_total(lines: list[str]) -> Decimal:
    """Extract total amount."""
    excluded_phrases = (
        "TOTAL DISCOUNT",
        "TOTAL DISCOUNT(S)",
        "TOTAL SAVINGS",
        "TOTAL SAVED",
        "TOTAL NUMBER",
        "TOTAL NUMBER OF ITEMS",
        "TOTAL ITEMS",
    )
    for i, line in enumerate(reversed(lines)):
        idx = len(lines) - 1 - i  # Original index
        line_upper = line.upper()
        # Skip lines like "TOTAL NUMBER OF ITEMS" - these are item counts, not the total amount
        if "TOTAL NUMBER" in line_upper:
            continue
        if any(phrase in line_upper for phrase in excluded_phrases):
            continue
        if "TOTAL" in line_upper and "SUBTOTAL" not in line_upper:
            # Try to find price on same line
            amount = _extract_price_from_line(line)
            if amount:
                return amount
            # Try next line first (most common: price is below TOTAL label)
            if idx + 1 < len(lines):
                amount = _extract_price_from_line(lines[idx + 1])
                if amount:
                    return amount
            # Try previous line as fallback (some receipts have price above TOTAL label)
            if idx > 0:
                prev_line = lines[idx - 1]
                prev_upper = prev_line.upper()
                # Don't grab tax/subtotal values as total
                if "TAX" not in prev_upper and "HST" not in prev_upper and "GST" not in prev_upper:
                    amount = _extract_price_from_line(prev_line)
                    if amount:
                        return amount
    return Decimal("0.00")


def _extract_tax(lines: list[str]) -> Decimal | None:
    """Extract tax amount (HST, GST, PST, TAX)."""
    if not lines:
        return None

    # Prefer tax in the summary block near the bottom of the receipt.
    # Anchor the search to the first summary-like line in the bottom half.
    anchor_idx = None
    start_search = max(0, len(lines) - max(20, len(lines) // 2))
    for i in range(start_search, len(lines)):
        upper = lines[i].upper()
        if "SUBTOTAL" in upper or "SUB TOTAL" in upper or "TOTAL AFTER TAX" in upper or upper.startswith("TOTAL"):
            anchor_idx = i
            break

    if anchor_idx is None:
        # Fallback: bottom quarter of receipt
        anchor_idx = max(0, len(lines) - max(10, len(lines) // 4))

    search_range = range(anchor_idx, len(lines))
    for i in search_range:
        line = lines[i]
        line_upper = line.upper()
        # Skip lines that are about subtotal or total (with or without space)
        if "SUBTOTAL" in line_upper or "SUB TOTAL" in line_upper:
            continue
        # Skip category headers like "TAXED GROCERY" and summary lines like "TOTAL AFTER TAX"
        if "TAXED" in line_upper or "TAXABLE" in line_upper:
            continue
        if "TOTAL" in line_upper and "AFTER TAX" in line_upper:
            continue
        # Skip TOTAL lines, but NOT lines like "(TOTAL GST+PST)" which indicate tax
        # Check if this is a tax-related total (contains both TOTAL and a tax keyword)
        has_total = "TOTAL" in line_upper
        has_tax_keyword = re.search(r"\b(HST|GST|PST|TAX)\b", line_upper) is not None
        if has_total and not has_tax_keyword:
            continue
        if has_tax_keyword:
            amount = _extract_price_from_line(line)
            # Use 'is not None' since Decimal("0.00") is falsy but valid
            if amount is not None:
                return amount
            # Try next line first (most common: price is below TAX label)
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                next_line_upper = next_line.upper()
                # Don't grab the TOTAL value as tax - check both the line itself
                # and the line after it (for format: "253.00" / "TOTAL")
                is_total_value = "TOTAL" in next_line_upper
                if not is_total_value and i + 2 < len(lines):
                    line_i2_upper = lines[i + 2].upper()
                    # Check if line i+2 contains TOTAL (meaning next line might be total value)
                    if "TOTAL" in line_i2_upper and "SUBTOTAL" not in line_i2_upper:
                        # But if TOTAL is followed by another price, then next line is tax, not total
                        # Format: [TAX] [tax_value] [TOTAL] [total_value]
                        if i + 3 < len(lines) and _extract_price_from_line(lines[i + 3]) is not None:
                            is_total_value = False  # Next line is actually tax
                        else:
                            is_total_value = True  # Next line is total (format: [TAX] [total] [TOTAL])
                # Only accept next line if it looks like a standalone price
                if not is_total_value and re.match(r"^\$?\s*\d+\.\d{2}\s*$", next_line):
                    amount = _extract_price_from_line(next_line)
                    if amount is not None:
                        return amount
            # Try previous line as fallback (some receipts have price above TAX label)
            if i > 0 and re.match(r"^\$?\s*\d+\.\d{2}\s*$", lines[i - 1]):
                prev_line_upper = lines[i - 1].upper()
                # Don't grab the SUBTOTAL value as tax
                if "SUBTOTAL" not in prev_line_upper and "TOTAL" not in prev_line_upper:
                    amount = _extract_price_from_line(lines[i - 1])
                    if amount is not None:
                        return amount
    return None


def _extract_subtotal(lines: list[str]) -> Decimal | None:
    """Extract subtotal amount."""
    for i, line in enumerate(lines):
        line_upper = line.upper()
        if "SUBTOTAL" in line_upper or "SUB TOTAL" in line_upper:
            amount = _extract_price_from_line(line)
            if amount:
                return amount
            # Try next line
            if i + 1 < len(lines):
                amount = _extract_price_from_line(lines[i + 1])
                if amount:
                    return amount
    return None


def _extract_price_from_line(line: str) -> Decimal | None:
    """Extract a price from a line of text."""
    # Look for price patterns: $XX.XX, XX.XX, etc.
    patterns = [
        r"\$?\s*(\d+\.\d{2})\s*$",  # Price at end of line
        r"\$?\s*(\d+\.\d{2})",  # Price anywhere
    ]
    for pattern in patterns:
        match = re.search(pattern, line)
        if match:
            try:
                return Decimal(match.group(1))
            except InvalidOperation:
                continue
    return None


def _extract_items(
    lines: list[str],
    summary_amounts: set[Decimal] | None = None,
    warning_sink: list[ReceiptWarning] | None = None,
) -> list[ReceiptItem]:
    """
    Extract line items from receipt.

    This is heuristic-based and will likely need manual correction.
    Handles multi-line item formats where description and price are on separate lines.

    Args:
        lines: List of text lines from the receipt
        summary_amounts: Set of Decimal amounts (total, tax, subtotal) to exclude from items
    """
    items: list[ReceiptItem] = []
    if summary_amounts is None:
        summary_amounts = set()

    # Skip header/footer sections
    skip_patterns = [
        # Total/subtotal patterns
        r"TOTAL",
        r"SUBTOTAL",
        r"SUB\s+TOTAL",
        r"TOTALS?\s+ON",
        # Tax patterns
        r"^TAX$",
        r"^HST",
        r"^GST",
        r"^PST",
        r"AFTER\s+TAX",
        r"\d+%$",  # Lines ending with percentage like "nst5%"
        # Payment patterns
        r"CASH",
        r"CREDIT",
        r"DEBIT",
        r"CHANGE",
        r"^BALANCE",
        r"VISA",
        r"MASTERCARD",
        r"AMEX",
        r"APPROVED",
        r"ACTIVATED",
        r"^PC\s+\d",  # Gift card / payment card lines like "PC 339918..."
        r"^ACCT:",
        r"^REFERENCE",
        # Footer patterns
        r"THANK YOU",
        r"WELCOME",
        r"RECEIPT",
        r"TRANSACTION",
        r"POINTS",
        r"REWARDS",
        r"EARNED",
        r"^SAVED$",
        r"^YOU SAVED",
        r"^CARD",
        r"AUTH",
        r"REF\s*#",
        r"SLIP\s*#",
        r"^TILL",
        r"CASHIER",
        r"\bSTORE\b",
        r"^PHONE",
        r"ADDRESS",
        r"SIGNATURE",
        r"Merchant",
        r"^QTY$",
        r"^UNIT$",
        r"^SAV$",
        r"ITEM\s+COUNT",
        r"NUMBER\s+OF\s+ITEMS",
        r"XXXX+",  # Masked card numbers
        r"^CAD",  # Payment amount lines like "CAD$ 5.00"
        r"VERIFIED",  # PIN verification
        r"^PIN$",
        r"CUSTOMER\s+COPY",  # Receipt copy marker
        r"COPY$",
        r"Optimum",  # PC Optimum loyalty program
        r"Redeemed",
    ]
    skip_regex = re.compile("|".join(skip_patterns), re.IGNORECASE)

    # Find where the items section ends (at TOTAL line) to avoid processing payment section
    total_line_idx = None
    for i, line in enumerate(lines):
        if re.search(r"\bTOTAL\b", line, re.IGNORECASE) and "SUBTOTAL" not in line.upper():
            total_line_idx = i
            break

    # First pass: identify item lines with prices (format: "DESCRIPTION ... PRICE H")
    # Common receipt format: "ITEM NAME    8.99 H" where H indicates taxable
    for i, line in enumerate(lines):
        # Stop processing after TOTAL line (rest is payment/footer section)
        if total_line_idx is not None and i > total_line_idx:
            break

        if skip_regex.search(line):
            continue

        # Skip very short lines or lines that are just numbers (item codes)
        if len(line) < 3:
            continue
        if re.match(r"^\d+$", line):
            continue

        # Skip quantity expressions - they'll be captured with their item in backward search
        # e.g., "3 @ $1.99", "2 /for $3.00", "1.22 lb @ $2.99/lb"
        # EXCEPT: Loblaw format "2 @ 2/$5.00 5.00" has trailing total price on same line
        is_qty_line = _looks_like_quantity_expression(line)
        has_trailing_total = re.search(r"\s+\d+\.\d{2}\s*[HhTt]?\s*$", line)
        if is_qty_line and not has_trailing_total:
            if warning_sink is not None and "/for" in line.lower():
                tail_token_match = re.search(r"([0-9A-Za-z]\.[0-9A-Za-z]{2,3}[HhTt]?)\s*$", line)
                tail_token = tail_token_match.group(1) if tail_token_match else ""
                if tail_token and any(c.isalpha() for c in tail_token):
                    context = line.strip()
                    if len(context) > 80:
                        context = context[:80]
                    warning_sink.append(
                        ReceiptWarning(
                            message=(
                                f'maybe missed item near malformed multi-buy total "{tail_token}"'
                                f' (context: "{context}")'
                            ),
                            after_item_index=(len(items) - 1) if items else None,
                        )
                    )
            continue

        # Skip lines that are just parenthetical codes like "( nel #44)", "(HHIT)".
        # Keep parenthetical promo lines that still carry a trailing item total.
        if re.match(r"^\([^)]*\)?$", line) and not re.search(r"\d+\.\d{2}\s*[HhTt]?\s*$", line):
            continue

        # Pattern 1: Price at end of line with optional H/tax marker
        # e.g., "SKITTLES GUMM 8.00 H" or "8.00 H" or "24.84"
        # Also handle discounts: "9.00- H" or "9.00-"
        match = re.search(r"(\d+\.\d{2})(-?)\s*[HhTt]?\s*$", line)
        if match:
            price = Decimal(match.group(1))
            is_discount = match.group(2) == "-"
            if is_discount:
                price = -price

            line_upper = line.upper()
            # Handle @REG$/REG$ promo lines.
            # If line is just a reg-price marker (single price), skip it.
            # If line includes both reg and sale prices, treat as price line for the item above.
            if "REG$" in line_upper or "@REG" in line_upper:
                prices = re.findall(r"(\d+\.\d{2})", line)
                # If previous line already contains a price, this is just promo info; skip it.
                if len(prices) > 1 and i > 0 and re.search(r"\d+\.\d{2}\s*[HhTt]?\s*$", lines[i - 1]):
                    continue

            # Skip if this is a summary line (contains TOTAL/SUBTOTAL keywords)
            # Don't skip just because the price matches - single-item receipts have item = total
            if "TOTAL" in line_upper or "SUBTOTAL" in line_upper or "SUB TOTAL" in line_upper:
                continue

            # Skip if previous line is a summary keyword and this is just the price
            if i > 0 and abs(price) in summary_amounts:
                prev_upper = lines[i - 1].upper()
                if "TOTAL" in prev_upper or "SUBTOTAL" in prev_upper or "SUB TOTAL" in prev_upper:
                    continue

            # Get description from same line (before the price)
            desc_part = line[: match.start()].strip()
            # Promo lines like "REG$8.99 5.99" should use the previous line as description
            force_backward = "REG$" in line_upper or "@REG" in line_upper

            # Clean up description - remove item codes at start
            if desc_part:
                desc_part = re.sub(r"^\d{8,}\s*", "", desc_part)

            # Priced aisle/section headers (e.g., "33-BAKERY INSTORE 12.00") should
            # use a nearby SKU-led item line, not the header text itself.
            is_priced_section_header = bool(desc_part) and _is_section_header_text(desc_part)
            if is_priced_section_header:
                desc_part = ""

            # Check if desc_part is valid: not empty, not too short, not a quantity expression
            # Quantity expressions like "2 @ 2/$5.00" should trigger backward search instead
            # Also handle promotional patterns like "(1 /for $2.99) 1 /for" from C&C receipts
            is_qty_expr = (
                (
                    _looks_like_quantity_expression(desc_part)
                    # Promotional pattern like "(#)<ON SALE)"
                    or re.match(r"^\([#\w]*\)\s*<?\s*ON\s*SALE", desc_part, re.IGNORECASE)
                )
                if desc_part
                else False
            )

            if desc_part and len(desc_part) > 2 and not is_qty_expr and not force_backward:
                items.append(
                    ReceiptItem(
                        description=desc_part,
                        price=price,
                        category=categorize_item(desc_part),
                    )
                )
            else:
                # Price on its own line - look backwards for description
                # Take the first valid candidate (closest to price line)
                qty_info = []
                qty_modifiers = []  # Store parsed quantity modifier data
                found_desc = None
                # For priced section headers, description usually follows on the next line
                # as a SKU-led item line (e.g., "62843020000 DOUGHNUTS MRJ").
                if is_priced_section_header:
                    for j in range(i + 1, min(i + 5, len(lines))):
                        next_line = lines[j].strip()
                        if not next_line:
                            continue
                        if skip_regex.search(next_line):
                            continue
                        if _looks_like_summary_line(next_line):
                            continue
                        if _looks_like_quantity_expression(next_line):
                            continue
                        if re.match(r"^\$?\d+\.\d{2}\s*[HhTt]?\s*$", next_line):
                            continue
                        if re.match(r"^\d{8,}\s*$", next_line):
                            continue
                        cleaned_next = _strip_leading_receipt_codes(next_line)
                        if not cleaned_next:
                            continue
                        if _is_section_header_text(cleaned_next):
                            continue
                        alpha_count = sum(1 for c in cleaned_next if c.isalpha())
                        alpha_ratio = alpha_count / len(cleaned_next) if cleaned_next else 0
                        if alpha_ratio < 0.5:
                            continue
                        found_desc = cleaned_next
                        break
                if found_desc is None:
                    for j in range(i - 1, max(i - 6, -1), -1):
                        prev_line = lines[j].strip()
                        # Skip if it's a price line, skip line, or item code
                        if re.match(r"^[\d.]+\s*[HhTt]?\s*$", prev_line):
                            continue
                        if re.match(r"^\d{8,}$", prev_line):
                            continue
                        if skip_regex.search(prev_line):
                            continue
                        # Check for quantity/weight modifier patterns first
                        # This extracts structured data from lines like "3 @ $1.99", "1.22 lb @"
                        modifier = _parse_quantity_modifier(prev_line)
                        if modifier:
                            qty_modifiers.append(modifier)
                            qty_info.append(prev_line)  # Keep raw text for fallback
                            continue
                        # Capture other quantity expressions that don't match our structured patterns
                        if _looks_like_quantity_expression(prev_line):
                            qty_info.append(prev_line)
                            continue
                        # Skip price-info lines: "$2.99 ea or 2/$5.00 KB", "$8.80/kg"
                        # These start with $ and contain unit prices or multi-buy offers
                        if re.match(r"^\$\d+\.\d{2}", prev_line):
                            continue
                        # Skip lines that are just parenthetical codes like "( nel #44)"
                        if re.match(r"^\([^)]*\)$", prev_line):
                            continue
                        # Skip incomplete parentheticals - start with ( but don't end with )
                        # These are often garbled OCR of Chinese text, e.g., "(Hi N" from "青蔥"
                        if prev_line.startswith("(") and not prev_line.endswith(")"):
                            continue
                        # Skip promotional/sale lines like "(#)<ON SALE)", "(KAE)<ON SALE)"
                        if re.match(r"^\([^)]*\)\s*<?\s*ON\s*SALE", prev_line, re.IGNORECASE):
                            continue
                        # Skip quantity expressions: "(1 /for $2.99) 1 /for", "(2 /for $4.50) 2 /for"
                        if re.match(r"^\(\d+\s*/\s*for\s+\$[\d.]+\)", prev_line):
                            continue
                        # Skip very short codes like "MRJ", "KB", "plo" (likely tax/sale markers or OCR noise)
                        if len(prev_line) <= 3:
                            continue
                        # Strip leading item code (digits) before calculating alpha ratio
                        # This handles Costco format: "1214759 GARLIC 3 LB"
                        desc_for_ratio = re.sub(r"^\d+\s*", "", prev_line)
                        # Calculate alpha ratio to filter garbled OCR lines
                        alpha_count = sum(1 for c in desc_for_ratio if c.isalpha())
                        alpha_ratio = alpha_count / len(desc_for_ratio) if desc_for_ratio else 0
                        # Skip garbled OCR lines (low alphabetic ratio, e.g., unrecognized Chinese)
                        if alpha_ratio < 0.5:
                            continue
                        if len(prev_line) > 2 and not re.match(r"^[\d.]+$", prev_line):
                            # Found a valid description - use it (proximity wins)
                            found_desc = prev_line
                            break

                if found_desc:
                    quantity = 1
                    description_suffix = ""

                    # Extract quantity from validated modifiers
                    if qty_modifiers:
                        # Use first modifier (closest to price line)
                        mod = qty_modifiers[0]
                        if _validate_quantity_price(price, mod):
                            quantity = mod.get("quantity", 1)
                            # Add weight info to description if present
                            if "weight" in mod:
                                description_suffix = f" ({mod['weight']} lb)"
                        else:
                            # Validation failed - append raw text as fallback
                            description_suffix = f" ({', '.join(reversed(qty_info))})"
                    elif qty_info:
                        # No structured modifiers but have raw qty text
                        description_suffix = f" ({', '.join(reversed(qty_info))})"

                    items.append(
                        ReceiptItem(
                            description=found_desc + description_suffix,
                            price=price,
                            quantity=quantity,
                            category=categorize_item(found_desc),  # Categorize on item name only
                        )
                    )
                elif warning_sink is not None and price > Decimal("0"):
                    context = line.strip()
                    if len(context) > 80:
                        context = context[:80]
                    message = f"maybe missed item near price {price:.2f}"
                    if context:
                        message += f' (context: "{context}")'
                    warning_sink.append(
                        ReceiptWarning(
                            message=message,
                            after_item_index=(len(items) - 1) if items else None,
                        )
                    )
        elif warning_sink is not None:
            # OCR can corrupt trailing prices (e.g., "8l.99", "1I.50"), causing
            # otherwise valid item lines to be skipped. Emit a review hint.
            malformed_price = re.search(r"(\d+[Il]\.\d{2}|\d+\.[Il]\d|\d+\.\d[Il])\s*[HhTt]?\s*$", line)
            if malformed_price:
                token = malformed_price.group(1)
                context = line.strip()
                if len(context) > 80:
                    context = context[:80]
                warning_sink.append(
                    ReceiptWarning(
                        message=(f'maybe missed item with malformed OCR price "{token}" (context: "{context}")'),
                        after_item_index=(len(items) - 1) if items else None,
                    )
                )
            # Multi-buy rows can also carry malformed totals like "2 /for S.OOH".
            # These indicate a likely missed item when no parseable trailing total exists.
            elif "/for" in line.lower() and re.search(r"\b[0-9A-Za-z]\.[0-9A-Za-z]{2,3}[HhTt]?\s*$", line):
                tail_token_match = re.search(r"([0-9A-Za-z]\.[0-9A-Za-z]{2,3}[HhTt]?)\s*$", line)
                tail_token = tail_token_match.group(1) if tail_token_match else ""
                if any(c.isalpha() for c in tail_token):
                    context = line.strip()
                    if len(context) > 80:
                        context = context[:80]
                    warning_sink.append(
                        ReceiptWarning(
                            message=(
                                f'maybe missed item near malformed multi-buy total "{tail_token}"'
                                f' (context: "{context}")'
                            ),
                            after_item_index=(len(items) - 1) if items else None,
                        )
                    )

    # Keep duplicates: repeated identical lines are common (e.g., two cartons
    # of the same milk/eggs with same price) and should remain separate items.
    return items
