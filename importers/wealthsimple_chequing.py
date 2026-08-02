"""
Wealthsimple chequing account statement importer for Beancount.

Handles the ``activities-export-YYYY-MM-DD.csv`` activity export. Unlike the
EQ Bank and Scotia exports, Wealthsimple publishes no running balance column,
so this importer emits transactions only and never balance assertions.
"""

from __future__ import annotations

import csv
import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from beancount.core import amount, data, flags
from beancount.ingest import importer
from beancount.ingest.cache import _FileMemo

from beanbeaver.domain.chequing_categorization import categorize_chequing_transaction
from beanbeaver.domain.chequing_import import parse_wealthsimple_rows
from beanbeaver.runtime import get_logger, load_chequing_categorization_patterns

logger = get_logger(__name__)


@dataclass
class WealthsimpleChequingTransaction:
    """Represents a single Wealthsimple chequing transaction."""

    date: datetime.date
    description: str
    amount: Decimal
    account: str
    currency: str = "CAD"

    def create_beancount_transaction(
        self, meta: dict[str, Any] | None = None, expense_account: str = "Expenses:Uncategorized"
    ) -> data.Transaction:
        """Create a beancount Transaction entry."""
        txn = data.Transaction(
            meta=meta or {},
            date=self.date,
            flag=flags.FLAG_OKAY,
            payee=self.description,
            narration="",
            tags=frozenset(),
            links=frozenset(),
            postings=[],
        )

        chequing_posting = data.Posting(
            self.account,
            amount.Amount(self.amount, self.currency),
            None,
            None,
            None,
            None,
        )
        counter_posting = data.Posting(
            expense_account,
            amount.Amount(-self.amount, self.currency),
            None,
            None,
            None,
            None,
        )

        txn.postings.append(chequing_posting)
        txn.postings.append(counter_posting)
        return txn


class WealthsimpleChequingImporter(importer.ImporterProtocol):
    """Wealthsimple chequing account CSV importer."""

    currency = "CAD"

    def __init__(self, account: str, categorization_patterns: list[tuple[str, str]] | None = None) -> None:
        if not account:
            raise ValueError("WealthsimpleChequingImporter requires a valid account name")
        self.account = account
        if categorization_patterns is None:
            self.categorization_patterns = list(load_chequing_categorization_patterns())
        else:
            self.categorization_patterns = categorization_patterns

    def identify(self, f: _FileMemo) -> bool:
        return True

    def file_account(self, f: _FileMemo) -> str:
        return self.account

    def file_date(self, f: _FileMemo) -> datetime.date | None:
        return None

    def _read_transactions(self, f: _FileMemo) -> list[WealthsimpleChequingTransaction]:
        with open(f.name, encoding="utf-8-sig") as csvfile:
            rows = list(csv.DictReader(csvfile))

        parsed_rows = parse_wealthsimple_rows(rows)
        skipped = len(rows) - len(parsed_rows)
        if skipped:
            logger.info("Skipped %d non-chequing/footer row(s) in Wealthsimple export", skipped)

        return [
            WealthsimpleChequingTransaction(
                date=date,
                description=description,
                amount=amount_val,
                account=self.account,
                currency=self.currency,
            )
            for date, description, amount_val, _balance in parsed_rows
        ]

    def extract(self, f: _FileMemo) -> list[data.Transaction]:
        entries: list[data.Transaction] = []
        for index, txn_data in enumerate(self._read_transactions(f)):
            category = categorize_chequing_transaction(
                txn_data.description,
                patterns=self.categorization_patterns,
            )
            expense_account = category or "Expenses:Uncategorized"

            meta = data.new_metadata("wealthsimple", index)
            entries.append(txn_data.create_beancount_transaction(meta=meta, expense_account=expense_account))

        return entries

    def extract_with_balances(self, f: _FileMemo) -> tuple[list[data.Transaction], list[tuple[datetime.date, Decimal]]]:
        """Extract transactions; the balance list is always empty.

        Wealthsimple's activity export has no running balance column, so there
        is nothing to assert against. The signature matches the other chequing
        importers so the import workflow can treat them uniformly.
        """
        return self.extract(f), []


CONFIG: list[WealthsimpleChequingImporter] = []
