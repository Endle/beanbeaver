"""Shared constants and helpers for OCR receipt parsing."""

from __future__ import annotations

from typing import Any

from .._rust import require_rust_matcher


def _is_section_header_text(text: str) -> bool:
    """Return True if text looks like a section/aisle header, not an item."""
    return bool(require_rust_matcher().receipt_is_section_header_text(text))


def _has_useful_bbox_data(pages: list[dict[str, Any]]) -> bool:
    """Check if the OCR result has useful bbox data for spatial parsing."""
    return bool(require_rust_matcher().receipt_has_useful_bbox_data(pages))


def _is_spatial_layout_receipt(_pages: list[dict[str, Any]], full_text: str) -> bool:
    """
    Detect if this receipt has a spatial layout where items and prices
    are on opposite sides of the same row (requiring bbox-based parsing).

    Examples: T&T, Real Canadian Superstore, and similar formats.
    """
    return bool(require_rust_matcher().receipt_is_spatial_layout_receipt(full_text))
