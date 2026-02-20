"""Tests for beanbeaver.ledger_access package exports."""

from __future__ import annotations

import beanbeaver.ledger_access as ledger_reader


def test_only_dto_api_is_exported() -> None:
    assert ledger_reader.__all__ == [
        "LedgerAmount",
        "LedgerPosting",
        "LedgerTransaction",
        "LedgerTransactionList",
        "apply_receipt_match",
        "list_transactions",
        "open_accounts",
        "transaction_dates_for_account",
        "validate_ledger",
    ]
    assert hasattr(ledger_reader, "LedgerAmount")
    assert hasattr(ledger_reader, "LedgerPosting")
    assert hasattr(ledger_reader, "LedgerTransaction")
    assert hasattr(ledger_reader, "LedgerTransactionList")
    assert hasattr(ledger_reader, "list_transactions")
    assert hasattr(ledger_reader, "open_accounts")
    assert hasattr(ledger_reader, "transaction_dates_for_account")
    assert hasattr(ledger_reader, "validate_ledger")
    assert hasattr(ledger_reader, "apply_receipt_match")
    assert not hasattr(ledger_reader, "get_ledger_reader")
    assert not hasattr(ledger_reader, "get_ledger_writer")
