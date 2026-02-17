"""Merchant/date/summary amount extraction helpers."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .common import MIN_LINE_CONFIDENCE


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


# TODO remove it
def _extract_date(_lines: list[str], full_text: str) -> date | None:
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
