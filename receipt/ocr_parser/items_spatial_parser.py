"""Spatial (bbox-based) receipt item extraction."""

import re
from decimal import Decimal
from typing import Any

from beanbeaver.domain.receipt import ReceiptItem, ReceiptWarning

from ..item_categories import ItemCategoryRuleLayers, categorize_item
from .common import (
    FOOTER_ADDRESS_PATTERNS,
    ITEM_X_THRESHOLD,
    MAX_ITEM_DISTANCE,
    MIN_CONFIDENCE,
    PRICE_X_THRESHOLD,
    Y_TOLERANCE,
    _clean_description,
    _get_word_x_center,
    _get_word_y_center,
    _is_price_word,
    _is_priced_generic_item_label,
    _is_section_header_text,
    _line_has_trailing_price,
    _looks_like_quantity_expression,
    _looks_like_summary_line,
    _strip_leading_receipt_codes,
)


def _extract_items_with_bbox(
    pages: list[dict[str, Any]],
    warning_sink: list[ReceiptWarning] | None = None,
    *,
    item_category_rule_layers: ItemCategoryRuleLayers,
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
                        category=categorize_item(description, rule_layers=item_category_rule_layers),
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
                            category=categorize_item(description, rule_layers=item_category_rule_layers),
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
