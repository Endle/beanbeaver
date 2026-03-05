from datetime import date
from decimal import Decimal

from beanbeaver.receipt.ocr_parser.fields_parser import _extract_date, _extract_tax, _extract_total


def test_extract_total_skips_discount_footer_total() -> None:
    lines = [
        "SUBTOTAL 69.03",
        "TAX 3.38",
        "TOTAL 72.41",
        "TOTAL NUMBER OF ITEMS SOLD",
        "TOTAL $ 5.00",
        "DISCOUNT(S",
    ]

    assert _extract_total(lines) == Decimal("72.41")


def test_extract_tax_scans_bottom_up_and_finds_summary_tax() -> None:
    lines = [
        "SUBTOTAL 69.03",
        "TAX 3.38",
        "TOTAL 72.41",
        "TOTAL NUMBER OF ITEMS SOLD",
        "TOTAL $ 5.00",
        "DISCOUNT(S",
    ]

    assert _extract_tax(lines) == Decimal("3.38")


def test_extract_date_parses_datetime_yy_mm_dd() -> None:
    lines = [
        "TOTAL 46.56",
        "DateTime: 26/03/03 19:47:12",
    ]

    assert _extract_date(lines, "\n".join(lines)) == date(2026, 3, 3)


def test_extract_date_does_not_slice_into_yyyy_mm_dd() -> None:
    lines = [
        "Purchase Date: 2026-03-03",
        "TOTAL 46.56",
    ]

    assert _extract_date(lines, "\n".join(lines)) == date(2026, 3, 3)
