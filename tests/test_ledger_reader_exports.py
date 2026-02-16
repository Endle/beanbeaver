"""Tests for beanbeaver.ledger_reader package exports."""

from __future__ import annotations

import beanbeaver.ledger_reader as ledger_reader


def test_only_getter_functions_are_exported() -> None:
    assert ledger_reader.__all__ == ["get_ledger_reader", "get_ledger_writer"]
    assert hasattr(ledger_reader, "get_ledger_reader")
    assert hasattr(ledger_reader, "get_ledger_writer")
    assert not hasattr(ledger_reader, "LedgerReader")
    assert not hasattr(ledger_reader, "LedgerWriter")
    assert not hasattr(ledger_reader, "LoadedLedger")
