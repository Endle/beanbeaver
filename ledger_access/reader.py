"""Centralized ledger access for Beancount files."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from beanbeaver.ledger_access._native import _native_backend
from beanbeaver.ledger_access._paths import default_main_beancount_path

DEFAULT_MAIN_BEANCOUNT_PATH = default_main_beancount_path()


@dataclass(frozen=True)
class LoadedLedger:
    """Structured result from loading a Beancount ledger."""

    path: Path
    entries: list[Any]
    errors: list[str]
    options: dict[str, Any]


class LedgerReader:
    """Read-only access to Beancount ledger data."""

    def __init__(self, default_ledger_path: Path | None = None) -> None:
        self.default_ledger_path = default_ledger_path or DEFAULT_MAIN_BEANCOUNT_PATH

    def _resolve_path(self, ledger_path: Path | str | None) -> Path:
        if ledger_path is None:
            return self.default_ledger_path
        return Path(ledger_path)

    def list_transactions_payload(
        self,
        ledger_path: Path | str | None = None,
    ) -> tuple[Path, list[dict[str, object]], list[str], dict[str, object]]:
        """Return plain transaction payloads from the native backend."""
        path = self._resolve_path(ledger_path)
        raw_path, transactions, errors, options = _native_backend.ledger_access_list_transactions(str(path))
        return Path(raw_path), list(transactions), list(errors), dict(options)

    def open_accounts(
        self,
        patterns: list[str],
        *,
        as_of: dt.date | None = None,
        ledger_path: Path | str | None = None,
    ) -> list[str]:
        """Return open account names matching any supplied fnmatch pattern."""
        if not patterns:
            return []

        if as_of is None:
            as_of = dt.date.today()

        path = self._resolve_path(ledger_path)
        return list(
            _native_backend.ledger_access_open_accounts(
                str(path),
                patterns,
                as_of.toordinal(),
            )
        )

    def open_credit_card_accounts(
        self,
        *,
        as_of: dt.date | None = None,
        ledger_path: Path | str | None = None,
        prefix: str = "Liabilities:CreditCard",
    ) -> list[str]:
        """Return currently open credit-card accounts under the given prefix."""
        normalized_prefix = prefix[:-1] if prefix.endswith(":") else prefix
        return self.open_accounts(
            patterns=[f"{normalized_prefix}:*"],
            as_of=as_of,
            ledger_path=ledger_path,
        )

    def transaction_dates_for_account(
        self,
        account: str,
        *,
        ledger_path: Path | str | None = None,
    ) -> set[dt.date]:
        """Return transaction dates where the given account appears in postings."""
        path = self._resolve_path(ledger_path)
        return {
            dt.date.fromordinal(ordinal)
            for ordinal in _native_backend.ledger_access_transaction_dates_for_account(str(path), account)
        }


_reader: LedgerReader | None = None


def get_ledger_reader() -> LedgerReader:
    """Return a singleton ledger reader instance."""
    global _reader
    if _reader is None:
        _reader = LedgerReader()
    return _reader
