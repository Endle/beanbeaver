"""Compatibility wrapper for receipt structuring field parsers."""

from beanbeaver.receipt.ocr_parser.fields_parser import (
    _extract_date,
    _extract_price_from_line,
    _extract_subtotal,
    _extract_tax,
    _extract_total,
)

__all__ = [
    "_extract_date",
    "_extract_price_from_line",
    "_extract_subtotal",
    "_extract_tax",
    "_extract_total",
]
