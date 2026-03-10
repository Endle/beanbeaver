"""Merchant/date/summary amount extraction helpers."""

import re
from datetime import date
from decimal import Decimal
from typing import Any

from .._rust import require_rust_matcher
from .common import MIN_LINE_CONFIDENCE, _normalize_decimal_spacing


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
    # Use word boundary matching to avoid matching substrings
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


_SEPARATED_DATE_PATTERN = re.compile(r"(?<!\d)(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})(?!\d)")
_COMPACT_DATE_PATTERN = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")
_MONTH_NAME_DATE_PATTERN = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE,
)
_DATE_CONTEXT_HINT = re.compile(r"\b(DATE(?:TIME)?|TRANS(?:ACTION)?\s*DATE)\b", re.IGNORECASE)


def _to_four_digit_year(year: int) -> int:
    """Convert 2-digit years to a century with POS-receipt-friendly defaults."""
    if year < 100:
        return 2000 + year if year <= 69 else 1900 + year
    return year


def _safe_date(year: int, month: int, day: int) -> date | None:
    """Return a valid date if inputs are in range, otherwise None."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _numeric_date_candidates(part1: str, part2: str, part3: str) -> list[tuple[date, str]]:
    """Generate plausible date candidates from a tokenized numeric date."""
    a = int(part1)
    b = int(part2)
    c = int(part3)
    candidates: list[tuple[date, str]] = []

    def add(year: int, month: int, day: int, kind: str) -> None:
        parsed = _safe_date(year, month, day)
        if parsed is not None:
            candidates.append((parsed, kind))

    if len(part1) == 4:
        add(a, b, c, "ymd4")
        return candidates

    if len(part3) == 4:
        # If one side is invalid month/day, format is effectively disambiguated.
        if a > 12 and b <= 12:
            add(c, b, a, "dmy4")
        elif b > 12 and a <= 12:
            add(c, a, b, "mdy4")
        else:
            # North America default first, then DD/MM/YYYY fallback.
            add(c, a, b, "mdy4")
            add(c, b, a, "dmy4")
        return candidates

    year_a = _to_four_digit_year(a)
    year_c = _to_four_digit_year(c)

    # YY/MM/DD appears in many payment terminal "DateTime" lines.
    if b <= 12 and c <= 31:
        add(year_a, b, c, "ymd2")

    if a <= 12 and b <= 31:
        add(year_c, a, b, "mdy2")
    if b <= 12 and a <= 31:
        add(year_c, b, a, "dmy2")

    return candidates


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
