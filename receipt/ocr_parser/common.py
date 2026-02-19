"""Shared constants and helpers for OCR receipt parsing."""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

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
    return re.search(r"\d+\.\d{2}\s*[HhTtJj]?\s*$", text.strip()) is not None


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


# TODO remove pages
def _is_spatial_layout_receipt(_pages: list[dict[str, Any]], full_text: str) -> bool:
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
