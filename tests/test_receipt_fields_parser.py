from datetime import date

from beanbeaver.receipt.ocr_parser.fields_parser import _extract_date


def test_extract_date_prefers_datetime_label_for_yy_mm_dd() -> None:
    lines = [
        "NOFRILLS",
        "DateTime: 26/03/03 19:47:12",
        "TOTAL 46.56",
    ]
    full_text = "\n".join(lines)

    assert _extract_date(lines, full_text) == date(2026, 3, 3)


def test_extract_date_defaults_to_mm_dd_yy_when_ambiguous() -> None:
    lines = [
        "Date: 03/04/26",
        "TOTAL 10.00",
    ]
    full_text = "\n".join(lines)

    assert _extract_date(lines, full_text) == date(2026, 3, 4)


def test_extract_date_supports_dd_mm_yy_when_month_is_impossible() -> None:
    lines = [
        "Date: 31/01/24",
        "TOTAL 10.00",
    ]
    full_text = "\n".join(lines)

    assert _extract_date(lines, full_text) == date(2024, 1, 31)


def test_extract_date_keeps_four_digit_year_dates() -> None:
    lines = [
        "Bestco Fresh Foodmart",
        "2026/02/20 15:15 Rece1pt# P9260220151502",
        "TOTAL 84.67",
    ]
    full_text = "\n".join(lines)

    assert _extract_date(lines, full_text) == date(2026, 2, 20)
