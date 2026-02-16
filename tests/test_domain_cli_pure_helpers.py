"""Tests for pure helpers extracted from CLI modules."""

from __future__ import annotations

from decimal import Decimal

from beanbeaver.domain.beancount_dates import extract_dates_from_beancount
from beanbeaver.domain.cc_import import build_result_file as build_cc_result_file
from beanbeaver.domain.chequing_import import (
    build_result_file as build_chequing_result_file,
    format_balance,
    format_transaction,
    latest_date,
    parse_eqbank_rows,
    parse_scotia_rows,
)
from beanbeaver.domain.match import (
    comment_block,
    find_transaction_end,
    itemized_receipt_total,
    match_key,
    transaction_charge_amount,
)


class _Units:
    def __init__(self, number: Decimal) -> None:
        self.number = number


class _Posting:
    def __init__(self, number: Decimal) -> None:
        self.units = _Units(number)


class _Transaction:
    def __init__(self, values: list[Decimal]) -> None:
        self.postings = [_Posting(v) for v in values]


class _Match:
    def __init__(self, file_path: str, line_number: int, txn: _Transaction | None) -> None:
        self.file_path = file_path
        self.line_number = line_number
        self.transaction = txn


class _Item:
    def __init__(self, total: Decimal) -> None:
        self.total = total


class _Receipt:
    def __init__(self, item_totals: list[str], tax: str | None = None) -> None:
        self.items = [_Item(Decimal(v)) for v in item_totals]
        self.tax = Decimal(tax) if tax is not None else None


def test_extract_dates_from_beancount() -> None:
    content = """
2025-01-15 * "txn" ""
2025-01-18 balance Assets:Bank:Chequing  100 CAD
2025-01-20 ! "txn2" ""
"""
    assert extract_dates_from_beancount(content) == ("0115", "0120")
    assert extract_dates_from_beancount(content, include_balance=True) == ("0115", "0120")


def test_cc_result_file_helper() -> None:
    assert (
        build_cc_result_file("Liabilities:CreditCard:Rogers:TestCard", "0101", "0131")
        == "rogers_testcard_0101_0131.beancount"
    )


def test_chequing_parse_and_format_helpers() -> None:
    eq_rows = [
        {
            "Transfer date": "2025-02-10",
            "Description": 'A "quoted" item',
            "Amount": "$-12.34",
            "Balance": "$100.00",
        }
    ]
    scotia_rows = [
        {
            "Date": "2025-02-11",
            "Description": "DEBIT",
            "Sub-description": "Grocery",
            "Amount": "-45.67",
            "Balance": "54.33",
        }
    ]
    parsed_eq = parse_eqbank_rows(eq_rows)
    parsed_scotia = parse_scotia_rows(scotia_rows)
    latest = latest_date(parsed_eq + parsed_scotia)
    assert latest is not None
    assert latest.isoformat() == "2025-02-11"

    txn = format_transaction(
        parsed_eq[0][0],
        parsed_eq[0][1],
        parsed_eq[0][2],
        "Assets:Bank:Chequing:EQBank",
        "Expenses:Test",
    )
    assert '\\"quoted\\"' in txn
    assert format_balance(
        parsed_scotia[0][0], "Assets:Bank:Chequing:Scotia", Decimal("54.33")
    ).startswith("2025-02-11 balance")
    assert build_chequing_result_file("0101", "0131", "eqbank") == "eqbank_chequing_0101_0131.beancount"


def test_match_helpers() -> None:
    lines = [
        '2025-01-01 * "A" ""\n',
        "  Assets:Card  -10 CAD\n",
        "  Expenses:Food  10 CAD\n",
        "\n",
        '2025-01-02 * "B" ""\n',
    ]
    assert find_transaction_end(lines, 0) == 4
    commented = comment_block(lines[:3])
    assert commented[0].startswith("; ")

    match = _Match("records/2025.beancount", 42, _Transaction([Decimal("10"), Decimal("-12.34")]))
    assert transaction_charge_amount(match) == Decimal("12.34")
    assert match_key(match) == ("records/2025.beancount", 42)

    receipt = _Receipt(["2.00", "3.00"], tax="0.50")
    assert itemized_receipt_total(receipt) == Decimal("5.50")
