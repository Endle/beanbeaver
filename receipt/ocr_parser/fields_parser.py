"""Merchant/date/summary amount extraction helpers."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

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


# TODO remove it
def _extract_date(lines: list[str], full_text: str) -> date | None:
    """Extract date from receipt (returns None if unknown)."""
    if not full_text and not lines:
        return None
    source_lines = lines or [line.strip() for line in full_text.split("\n") if line.strip()]
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
    current_year = date.today().year
    current_yy = current_year % 100

    ranked_candidates: list[tuple[int, int, int, date]] = []
    for line_idx, line in enumerate(source_lines):
        normalized_line = _normalize_decimal_spacing(line)
        hint_bonus = 40 if _DATE_CONTEXT_HINT.search(normalized_line) else 0
        prefer_year_first = hint_bonus > 0

        for match in _SEPARATED_DATE_PATTERN.finditer(normalized_line):
            part1, part2, part3 = match.groups()
            for parsed, kind in _numeric_date_candidates(part1, part2, part3):
                if kind == "ymd2":
                    # Treat ambiguous short dates as YY/MM/DD only in date-labeled context.
                    year_token = int(part1)
                    if not (prefer_year_first and 20 <= year_token <= current_yy + 1):
                        continue
                base = {
                    "ymd4": 35,
                    "ymd2": 28,
                    "mdy4": 25,
                    "dmy4": 24,
                    "mdy2": 22,
                    "dmy2": 20,
                }.get(kind, 0)
                # Keep a weak North America bias for ambiguous short dates.
                if kind == "mdy2":
                    base += 2
                if kind == "ymd2" and prefer_year_first:
                    base += 3
                year_score = max(0, 10 - abs(parsed.year - current_year))
                ranked_candidates.append((base + hint_bonus + year_score, line_idx, match.start(), parsed))

        for match in _COMPACT_DATE_PATTERN.finditer(normalized_line):
            parsed = _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            if parsed is not None:
                year_score = max(0, 10 - abs(parsed.year - current_year))
                ranked_candidates.append((30 + hint_bonus + year_score, line_idx, match.start(), parsed))

        for match in _MONTH_NAME_DATE_PATTERN.finditer(normalized_line):
            month = month_map.get(match.group(1)[:3].lower())
            if month is None:
                continue
            parsed = _safe_date(int(match.group(3)), month, int(match.group(2)))
            if parsed is not None:
                year_score = max(0, 10 - abs(parsed.year - current_year))
                ranked_candidates.append((26 + hint_bonus + year_score, line_idx, match.start(), parsed))

    if not ranked_candidates:
        return None

    ranked_candidates.sort(key=lambda x: (-x[0], x[1], x[2]))
    return ranked_candidates[0][3]


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
            prev_upper = lines[idx - 1].upper() if idx > 0 else ""
            next_upper = lines[idx + 1].upper() if idx + 1 < len(lines) else ""
            # Skip footer discount totals like:
            #   TOTAL NUMBER OF ITEMS SOLD
            #   TOTAL $ 5.00
            #   DISCOUNT(S)
            if "DISCOUNT" in next_upper:
                continue
            if "TOTAL NUMBER OF ITEMS SOLD" in prev_upper:
                continue
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
    # Scan bottom-up to prefer summary/footer tax lines while avoiding over-narrow anchors.
    for i in range(len(lines) - 1, -1, -1):
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
    line = _normalize_decimal_spacing(line)
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
