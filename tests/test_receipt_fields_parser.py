from decimal import Decimal

from beanbeaver.receipt.ocr_parser.fields_parser import _extract_tax, _extract_total


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
