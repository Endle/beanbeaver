"""Shared constants and helpers for OCR receipt parsing."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .._rust import require_rust_matcher

# Minimum average confidence for a line to be considered reliable
MIN_LINE_CONFIDENCE = 0.6

# Bbox-based parsing constants
MIN_CONFIDENCE = 0.5
PRICE_X_THRESHOLD = 0.65
ITEM_X_THRESHOLD = 0.6
Y_TOLERANCE = 0.02
MAX_ITEM_DISTANCE = 0.08


def _normalize_decimal_spacing(text: str) -> str:
    """Normalize OCR-split decimal tokens like ``3. 50`` to ``3.50``."""
    return str(require_rust_matcher().receipt_normalize_decimal_spacing(text))


def _is_section_header_text(text: str) -> bool:
    """Return True if text looks like a section/aisle header, not an item."""
    return bool(require_rust_matcher().receipt_is_section_header_text(text))


def _strip_leading_receipt_codes(text: str) -> str:
    """Remove leading quantity/SKU prefixes from an OCR item line."""
    return str(require_rust_matcher().receipt_strip_leading_receipt_codes(text))


def _looks_like_summary_line(text: str) -> bool:
    """Return True if text appears to be a summary/tax/payment line."""
    return bool(require_rust_matcher().receipt_looks_like_summary_line(text))


def _looks_like_receipt_metadata_line(text: str) -> bool:
    """Return True for operational/header/footer lines that are not items."""
    return bool(require_rust_matcher().receipt_looks_like_receipt_metadata_line(text))


def _line_has_trailing_price(text: str) -> bool:
    """Return True if the line itself ends with a price."""
    return bool(require_rust_matcher().receipt_line_has_trailing_price(text))


def _looks_like_onsale_marker(text: str) -> bool:
    """Return True for standalone sale markers like ONSALE/ONSAL tokens."""
    return bool(require_rust_matcher().receipt_looks_like_onsale_marker(text))


def _is_priced_generic_item_label(left_text: str, full_text: str) -> bool:
    """Allow short generic labels when they clearly carry an item price."""
    return bool(require_rust_matcher().receipt_is_priced_generic_item_label(left_text, full_text))


def _parse_quantity_modifier(line: str) -> dict[str, Any] | None:
    """Parse quantity/weight modifier from a line."""
    return require_rust_matcher().receipt_parse_quantity_modifier(line)


def _validate_quantity_price(
    total_price: Decimal,
    modifier: dict[str, Any],
    tolerance: Decimal = Decimal("0.02"),
) -> bool:
    """Validate that quantity × unit_price ≈ total_price."""
    return bool(require_rust_matcher().receipt_validate_quantity_price(total_price, modifier, tolerance))


def _looks_like_quantity_expression(text: str) -> bool:
    """
    Return True if text is a quantity/offer modifier line, not an item description.

    This intentionally avoids broad slash-based matching so product names like
    "50/70 SHRIMP" are not misclassified as quantity lines.
    """
    return bool(require_rust_matcher().receipt_looks_like_quantity_expression(text))


def _bbox_edges(value: Any) -> tuple[float, float, float, float]:
    """Return normalized bbox edges from either legacy or canonical bbox data."""
    if isinstance(value, dict):
        left = float(value.get("left", 0.0))
        top = float(value.get("top", 0.0))
        right = float(value.get("right", left))
        bottom = float(value.get("bottom", top))
        return left, top, right, bottom

    if isinstance(value, (list, tuple)) and len(value) >= 2:
        first = value[0]
        second = value[1]
        if (
            isinstance(first, (list, tuple))
            and len(first) >= 2
            and isinstance(second, (list, tuple))
            and len(second) >= 2
        ):
            left = float(first[0])
            top = float(first[1])
            right = float(second[0])
            bottom = float(second[1])
            return left, top, right, bottom

    return 0.0, 0.0, 0.0, 0.0


def _get_word_y_center(word: dict[str, Any]) -> float:
    """Get the vertical center of a word from its bbox."""
    _, y_top, _, y_bottom = _bbox_edges(word.get("bbox"))
    return (y_top + y_bottom) / 2


def _get_word_x_center(word: dict[str, Any]) -> float:
    """Get the horizontal center of a word from its bbox."""
    x_left, _, x_right, _ = _bbox_edges(word.get("bbox"))
    return (x_left + x_right) / 2


def _is_price_word(word: dict[str, Any]) -> Decimal | None:
    """Check if a word is a price pattern. Returns the price or None."""
    result = require_rust_matcher().receipt_extract_price_word(str(word.get("text", "")))
    if result is None:
        return None
    try:
        return Decimal(str(result))
    except InvalidOperation:
        return None


def _clean_description(desc: str) -> str:
    """Clean up item description from OCR artifacts."""
    return str(require_rust_matcher().receipt_clean_description(desc))


def _has_useful_bbox_data(pages: list[dict[str, Any]]) -> bool:
    """Check if the OCR result has useful bbox data for spatial parsing."""
    return bool(require_rust_matcher().receipt_has_useful_bbox_data(pages))


# TODO remove pages
def _is_spatial_layout_receipt(_pages: list[dict[str, Any]], full_text: str) -> bool:
    """
    Detect if this receipt has a spatial layout where items and prices
    are on opposite sides of the same row (requiring bbox-based parsing).

    Examples: T&T, Real Canadian Superstore, and similar formats.
    """
    return bool(require_rust_matcher().receipt_is_spatial_layout_receipt(full_text))
