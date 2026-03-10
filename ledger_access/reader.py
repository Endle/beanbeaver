"""Compatibility wrappers over the public ledger_access API."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from beanbeaver.ledger_access.api import (
    DEFAULT_MAIN_BEANCOUNT_PATH,
    list_transactions,
    open_accounts,
    transaction_dates_for_account,
)


@dataclass(frozen=True)
class LoadedLedger:
    path: Path
    entries: list[Any]
    errors: list[str]
    options: dict[str, Any]


class LedgerReader:
    def __init__(self, default_ledger_path: Path | None = None) -> None:
        self.default_ledger_path = default_ledger_path or DEFAULT_MAIN_BEANCOUNT_PATH

    def _resolve_path(self, ledger_path: Path | str | None) -> Path:
        return self.default_ledger_path if ledger_path is None else Path(ledger_path)

    def list_transactions_payload(self, ledger_path: Path | str | None = None) -> tuple[Path, list[dict[str, object]], list[str], dict[str, object]]:
        result = list_transactions(ledger_path=self._resolve_path(ledger_path))
        payload = [
            {
                "date_ordinal": txn.date.toordinal(),
                "payee": txn.payee,
                "narration": txn.narration,
                "postings": [
                    {
                        "account": posting.account,
                        "number_str": None if posting.units is None else str(posting.units.number),
                        "currency": None if posting.units is None else posting.units.currency,
                    }
                    for posting in txn.postings
                ],
                "file_path": txn.file_path,
                "line_number": txn.line_number,
            }
            for txn in result.transactions
        ]
        return result.path, payload, result.errors, result.options

    def open_accounts(self, patterns: list[str], *, as_of: dt.date | None = None, ledger_path: Path | str | None = None) -> list[str]:
        return open_accounts(patterns, as_of=as_of, ledger_path=self._resolve_path(ledger_path))

    def open_credit_card_accounts(self, *, as_of: dt.date | None = None, ledger_path: Path | str | None = None, prefix: str = "Liabilities:CreditCard") -> list[str]:
        normalized_prefix = prefix[:-1] if prefix.endswith(":") else prefix
        return self.open_accounts([f"{normalized_prefix}:*"], as_of=as_of, ledger_path=ledger_path)

    def transaction_dates_for_account(self, account: str, *, ledger_path: Path | str | None = None) -> set[dt.date]:
        return transaction_dates_for_account(account, ledger_path=self._resolve_path(ledger_path))


_reader: LedgerReader | None = None


def get_ledger_reader() -> LedgerReader:
    global _reader
    if _reader is None:
        _reader = LedgerReader()
    return _reader
