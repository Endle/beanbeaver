"""Merchant/date/summary amount extraction helpers."""

from datetime import date
from decimal import Decimal
from typing import Any

from .._rust import require_rust_matcher


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
    return require_rust_matcher().receipt_extract_merchant(
        lines,
        full_text,
        pages or [],
        list(known_merchants) if known_merchants is not None else None,
    )


def _extract_date(lines: list[str], full_text: str, *, reference_date: date | None = None) -> date | None:
    """Extract date from receipt (returns None if unknown)."""
    result = require_rust_matcher().receipt_extract_date(
        lines,
        full_text,
        (reference_date or date.today()).year,
    )
    if result is None:
        return None
    year, month, day = result
    return date(int(year), int(month), int(day))


def _extract_total(lines: list[str]) -> Decimal:
    """Extract total amount."""
    cents = int(require_rust_matcher().receipt_extract_total(lines))
    return Decimal(cents) / Decimal("100")


def _extract_tax(lines: list[str]) -> Decimal | None:
    """Extract tax amount (HST, GST, PST, TAX)."""
    cents = require_rust_matcher().receipt_extract_tax(lines)
    if cents is None:
        return None
    return Decimal(int(cents)) / Decimal("100")


def _extract_subtotal(lines: list[str]) -> Decimal | None:
    """Extract subtotal amount."""
    cents = require_rust_matcher().receipt_extract_subtotal(lines)
    if cents is None:
        return None
    return Decimal(int(cents)) / Decimal("100")


def _extract_price_from_line(line: str) -> Decimal | None:
    """Extract a price from a line of text."""
    cents = require_rust_matcher().receipt_extract_price_from_line(line)
    if cents is None:
        return None
    return Decimal(int(cents)) / Decimal("100")
