"""
EQ Bank chequing account statement importer for Beancount.

Handles CSV exports from EQ Bank chequing accounts with balance assertion support.
"""

import csv
import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from beancount.core import amount, data, flags
from beancount.ingest import importer
from beancount.ingest.cache import _FileMemo

from beanbeaver.domain.chequing_import import next_day


@dataclass
class ChequingTransaction:
    """Represents a single chequing account transaction."""

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

        # Posting to the chequing account
        chequing_posting = data.Posting(
            self.account,
            amount.Amount(self.amount, self.currency),
            None,
            None,
            None,
            None,
        )

        # Counter posting to expense/income account
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


class EQBankChequingImporter(importer.ImporterProtocol):
    """EQ Bank chequing account CSV importer."""

    currency = "CAD"
    date_format = "%Y-%m-%d"

    def __init__(self, account: str) -> None:
        if not account:
            raise ValueError("EQBankChequingImporter requires a valid account name")
        self.account = account

    def identify(self, f: _FileMemo) -> bool:
        return True

    def file_account(self, f: _FileMemo) -> str:
        return self.account

    def file_date(self, f: _FileMemo) -> datetime.date | None:
        return None

    def _parse_amount(self, amount_str: str) -> Decimal:
        """Parse amount string like '-$53.86' or '$2515.80' to Decimal."""
        # Remove $ and , characters
        cleaned = amount_str.replace("$", "").replace(",", "")
        return Decimal(cleaned)

    def _parse_row(self, row: dict[str, str], index: int) -> ChequingTransaction:
        """Parse a CSV row into a ChequingTransaction."""
        date = datetime.datetime.strptime(row["Transfer date"], self.date_format).date()
        description = row["Description"]
        amount_val = self._parse_amount(row["Amount"])
        balance = self._parse_amount(row["Balance"])

        return ChequingTransaction(
            date=date,
            description=description,
            amount=amount_val,
            balance=balance,
            account=self.account,
            currency=self.currency,
        )

    def extract(self, f: _FileMemo) -> list[data.Transaction]:
        """Extract transactions from the CSV file."""
        entries: list[data.Transaction] = []

        with open(f.name, encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for index, row in enumerate(reader):
                txn_data = self._parse_row(row, index)

                # Determine the counter account
                expense_account = "Expenses:Uncategorized"

                meta = data.new_metadata("eqbank", index)
                txn = txn_data.create_beancount_transaction(meta=meta, expense_account=expense_account)
                entries.append(txn)

        return entries

    def extract_with_balances(self, f: _FileMemo) -> tuple[list[data.Transaction], list[tuple[datetime.date, Decimal]]]:
        """
        Extract transactions and balance data from the CSV file.

        Returns:
            Tuple of (transactions, balances) where balances is a list of
            (date, balance_amount) tuples for potential balance directives.
        """
        entries: list[data.Transaction] = []
        balances: list[tuple[datetime.date, Decimal]] = []

        with open(f.name, encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for index, row in enumerate(reader):
                txn_data = self._parse_row(row, index)

                # Determine the counter account
                expense_account = "Expenses:Uncategorized"

                meta = data.new_metadata("eqbank", index)
                txn = txn_data.create_beancount_transaction(meta=meta, expense_account=expense_account)
                entries.append(txn)

                # Record balance for this date (balance is after the transaction)
                # Balance directive date is the day after the transaction
                balances.append((next_day(txn_data.date), txn_data.balance))

        return entries, balances


# Configuration for bean-extract
CONFIG: list[EQBankChequingImporter] = []
