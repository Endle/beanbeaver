"""Behavioral tests for the National Bank credit card importer."""

from __future__ import annotations

from pathlib import Path

from beanbeaver.importers import NationalBankImporter
from beancount.core import data

# Semicolon-delimited export. Card numbers are masked; merchants are brands only.
_CSV = (
    'Date;"card Number";Description;Category;Debit;Credit\n'
    '"2026-06-29";"************5839";"T&T Supermarket";Groceries;"49.59";"0"\n'
    '"2026-06-29";"************5839";Chipotle;"Fast food";"15.2";"0"\n'
    '"2026-05-22";"************2349";"Annual fee";Fees;"150.0";"0"\n'
    '"2026-06-15";"************5839";"PAIEMENT";Payment;"0";"200.00"\n'
    '"2026-06-10";"************5839";"Store Refund";Groceries;"0";"12.34"\n'
)


class _FileMemo:
    def __init__(self, name: str) -> None:
        self.name = name


def _extract(tmp_path: Path) -> list[data.Transaction]:
    csv_path = tmp_path / "2026-07-01-195935.csv"
    csv_path.write_text(_CSV, encoding="utf-8")
    importer = NationalBankImporter(account="Liabilities:CreditCard:NationalBank:CardA")
    return importer.extract(_FileMemo(str(csv_path)))


def _card_posting(txn: data.Transaction) -> data.Posting:
    return next(p for p in txn.postings if p.account.startswith("Liabilities"))


def test_imports_only_debit_rows(tmp_path: Path) -> None:
    entries = _extract(tmp_path)

    # Three debit rows import; the payment and refund (credit-only) rows are skipped.
    payees = [txn.payee for txn in entries]
    assert payees == ["T&T Supermarket", "Chipotle", "Annual fee"]


def test_card_posting_negates_debit_amount(tmp_path: Path) -> None:
    entries = _extract(tmp_path)
    by_payee = {txn.payee: txn for txn in entries}

    assert str(_card_posting(by_payee["T&T Supermarket"]).units.number) == "-49.59"
    assert str(_card_posting(by_payee["Chipotle"]).units.number) == "-15.2"
    assert str(_card_posting(by_payee["Annual fee"]).units.number) == "-150.0"
    assert _card_posting(by_payee["Annual fee"]).units.currency == "CAD"


def test_dates_parsed_from_iso_column(tmp_path: Path) -> None:
    entries = _extract(tmp_path)
    dates = {txn.payee: txn.date.isoformat() for txn in entries}

    assert dates["T&T Supermarket"] == "2026-06-29"
    assert dates["Annual fee"] == "2026-05-22"
