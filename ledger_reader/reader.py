"""Centralized ledger access for Beancount files.

This module is intended to be the single place that reads ledger files from disk.
Other components should gradually migrate to consume this API instead of calling
`beancount.loader.load_file()` directly.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from beanbeaver.runtime import get_logger, get_paths
from beancount.core import data
from beancount.loader import load_file

logger = get_logger(__name__)


@dataclass(frozen=True)
class LoadedLedger:
    """Structured result from loading a Beancount ledger."""

    path: Path
    entries: list[data.Directive]
    errors: list[Any]
    options: dict[str, Any]


class LedgerReader:
    """Read-only access to Beancount ledger data."""

    def __init__(self, default_ledger_path: Path | None = None) -> None:
        self.default_ledger_path = default_ledger_path or get_paths().main_beancount

    def _resolve_path(self, ledger_path: Path | str | None) -> Path:
        if ledger_path is None:
            return self.default_ledger_path
        return Path(ledger_path)

    def load(self, ledger_path: Path | str | None = None) -> LoadedLedger:
        """Load ledger entries from disk."""
        path = self._resolve_path(ledger_path)
        entries, errors, options = load_file(str(path))

        if errors:
            logger.warning("Beancount reported %d error(s) while loading %s", len(errors), path)

        return LoadedLedger(
            path=path,
            entries=list(entries),
            errors=list(errors),
            options=dict(options),
        )

    def transactions(self, ledger_path: Path | str | None = None) -> list[data.Transaction]:
        """Return all transactions from the ledger."""
        loaded = self.load(ledger_path=ledger_path)
        return [entry for entry in loaded.entries if isinstance(entry, data.Transaction)]

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

        loaded = self.load(ledger_path=ledger_path)

        last_open: dict[str, dt.date] = {}
        last_close: dict[str, dt.date] = {}

        for entry in loaded.entries:
            if isinstance(entry, data.Open):
                last_open[entry.account] = entry.date
            elif isinstance(entry, data.Close):
                last_close[entry.account] = entry.date

        def is_open(account: str) -> bool:
            opened = last_open.get(account)
            if not opened or opened > as_of:
                return False
            closed = last_close.get(account)
            return closed is None or closed > as_of

        matches: list[str] = []
        for account in last_open:
            if not is_open(account):
                continue
            for pattern in patterns:
                if fnmatch.fnmatch(account, pattern):
                    matches.append(account)
                    break

        return sorted(matches)

    def open_credit_card_accounts(
        self,
        *,
        as_of: dt.date | None = None,
        ledger_path: Path | str | None = None,
        prefix: str = "Liabilities:CreditCard",
    ) -> list[str]:
        """Return currently open credit-card accounts under the given prefix."""
        return self.open_accounts(
            patterns=[f"{prefix}*"],
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
        loaded = self.load(ledger_path=ledger_path)
        dates: set[dt.date] = set()
        for entry in loaded.entries:
            if not isinstance(entry, data.Transaction):
                continue
            if any(posting.account == account for posting in entry.postings):
                dates.add(entry.date)
        return dates


_reader: LedgerReader | None = None


def get_ledger_reader() -> LedgerReader:
    """Return a singleton ledger reader instance."""
    global _reader
    if _reader is None:
        _reader = LedgerReader()
    return _reader
