"""
Scotiabank chequing account statement importer for Beancount.

Handles CSV exports from Scotia chequing accounts with balance assertion support.
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
from beanbeaver.domain.chequing_import import next_day
from beanbeaver.runtime import load_chequing_categorization_patterns


@dataclass
class ScotiaChequingTransaction:
    """Represents a single Scotia chequing transaction."""

    date: datetime.date
    description: str
    amount: Decimal
    balance: Decimal
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


class ScotiaChequingImporter(importer.ImporterProtocol):
    """Scotia chequing account CSV importer."""

    currency = "CAD"
    date_format = "%Y-%m-%d"

    def __init__(self, account: str, categorization_patterns: list[tuple[str, str]] | None = None) -> None:
        if not account:
            raise ValueError("ScotiaChequingImporter requires a valid account name")
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

    @staticmethod
    def _parse_amount(amount_str: str) -> Decimal:
        cleaned = amount_str.replace(",", "").replace("$", "")
        return Decimal(cleaned)

    @staticmethod
    def _combine_description(description: str, sub_description: str) -> str:
        desc = description.strip()
        sub = sub_description.strip()
        if sub:
            return f"{desc} - {sub}"
        return desc

    def _parse_row(self, row: dict[str, str], index: int) -> ScotiaChequingTransaction:
        date = datetime.datetime.strptime(row["Date"], self.date_format).date()
        description = self._combine_description(row["Description"], row.get("Sub-description", ""))
        amount_val = self._parse_amount(row["Amount"])
        balance = self._parse_amount(row["Balance"])

        return ScotiaChequingTransaction(
            date=date,
            description=description,
            amount=amount_val,
            balance=balance,
            account=self.account,
            currency=self.currency,
        )

    def extract(self, f: _FileMemo) -> list[data.Transaction]:
        entries: list[data.Transaction] = []
        with open(f.name, encoding="utf-8-sig") as csvfile:
            reader = csv.DictReader(csvfile)
            for index, row in enumerate(reader):
                if not row.get("Date"):
                    continue
                txn_data = self._parse_row(row, index)

                category = categorize_chequing_transaction(
                    txn_data.description,
                    patterns=self.categorization_patterns,
                )
                expense_account = category or "Expenses:Uncategorized"

                meta = data.new_metadata("scotia", index)
                txn = txn_data.create_beancount_transaction(meta=meta, expense_account=expense_account)
                entries.append(txn)

        return entries

    def extract_with_balances(self, f: _FileMemo) -> tuple[list[data.Transaction], list[tuple[datetime.date, Decimal]]]:
        entries: list[data.Transaction] = []
        balances: list[tuple[datetime.date, Decimal]] = []

        with open(f.name, encoding="utf-8-sig") as csvfile:
            reader = csv.DictReader(csvfile)
            for index, row in enumerate(reader):
                if not row.get("Date"):
                    continue
                txn_data = self._parse_row(row, index)

                category = categorize_chequing_transaction(
                    txn_data.description,
                    patterns=self.categorization_patterns,
                )
                expense_account = category or "Expenses:Uncategorized"

                meta = data.new_metadata("scotia", index)
                txn = txn_data.create_beancount_transaction(meta=meta, expense_account=expense_account)
                entries.append(txn)

                balances.append((next_day(txn_data.date), txn_data.balance))

        return entries, balances


CONFIG: list[ScotiaChequingImporter] = []
