"""Tests for the public DTO-facing ledger_access API."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from beanbeaver.ledger_access import (
    LedgerTransaction,
    list_transactions,
    open_accounts,
    transaction_dates_for_account,
    validate_ledger,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_list_transactions_returns_dto_objects(tmp_path: Path) -> None:
    ledger = tmp_path / "main.beancount"
    _write(
        ledger,
        """
option "operating_currency" "CAD"
2025-01-01 open Liabilities:CreditCard:CardA CAD
2025-01-01 open Expenses:Food CAD
2025-01-02 * "Grocery" ""
  Liabilities:CreditCard:CardA -10.00 CAD
  Expenses:Food 10.00 CAD
""".lstrip(),
    )

    result = list_transactions(ledger_path=ledger)
    assert result.path == ledger
    assert not result.errors
    assert len(result.transactions) == 1

    txn = result.transactions[0]
    assert isinstance(txn, LedgerTransaction)
    assert txn.date.isoformat() == "2025-01-02"
    assert txn.payee == "Grocery"
    assert txn.line_number > 0
    assert txn.file_path
    assert len(txn.postings) == 2
    assert txn.postings[0].units is not None
    assert str(txn.postings[0].units.number) == "-10.00"
    assert txn.postings[0].units.currency == "CAD"


def test_open_accounts_and_transaction_dates_wrappers(tmp_path: Path) -> None:
    ledger = tmp_path / "main.beancount"
    _write(
        ledger,
        """
option "operating_currency" "CAD"
2025-01-01 open Liabilities:CreditCard:CardA CAD
2025-01-02 * "T1" ""
  Liabilities:CreditCard:CardA -10.00 CAD
  Expenses:Food 10.00 CAD
""".lstrip(),
    )

    accounts = open_accounts(["Liabilities:CreditCard:*"], as_of=date(2025, 1, 3), ledger_path=ledger)
    assert accounts == ["Liabilities:CreditCard:CardA"]

    dates = transaction_dates_for_account("Liabilities:CreditCard:CardA", ledger_path=ledger)
    assert dates == {date(2025, 1, 2)}


def test_validate_ledger_returns_string_errors(tmp_path: Path) -> None:
    ledger = tmp_path / "main.beancount"
    _write(ledger, "this is not valid beancount\n")

    errors = validate_ledger(ledger_path=ledger)
    assert errors
    assert all(isinstance(err, str) for err in errors)
