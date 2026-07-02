"""National Bank of Canada credit card importer.

National Bank exports a semicolon-delimited CSV with a header of::

    Date;"card Number";Description;Category;Debit;Credit

Purchases land in the ``Debit`` column; payments and refunds land in the
``Credit`` column. Following the CIBC importer's precedent (and the repo-wide
"prefer missing over wrong" principle), only debit rows are imported. Credit-only
rows are skipped: without a transaction-type column there is no reliable way to
tell a bill payment apart from a merchant refund, and recording a payment as a
negative expense would be a real error.
"""

from __future__ import annotations

import csv
from collections.abc import Generator
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from .base import BaseCardImporter

if TYPE_CHECKING:
    from beancount.ingest.cache import _FileMemo

# Column indices in the semicolon-delimited export.
_DATE = 0
_CARD_NUMBER = 1
_DESCRIPTION = 2
_CATEGORY = 3
_DEBIT = 4
_CREDIT = 5


class NationalBankImporter(BaseCardImporter):
    """National Bank of Canada credit cards.

    Example:
        importer = NationalBankImporter(account="Liabilities:CreditCard:NationalBank:CardA")
    """

    date_format = "%Y-%m-%d"
    # utf-8-sig tolerates a possible BOM and handles accented French descriptions.
    encoding = "utf-8-sig"

    def identify(self, f: _FileMemo) -> bool:
        return True

    def read_rows(self, f: _FileMemo) -> Generator[list[str], None, None]:
        """Yield data rows from the semicolon-delimited export (header skipped)."""
        with open(f.name, encoding=self.encoding) as file:
            reader = csv.reader(file, delimiter=";")
            next(reader, None)  # header
            for row in reader:
                if row:
                    yield row

    @staticmethod
    def _to_decimal(value: str) -> Decimal:
        cleaned = value.strip().lstrip("$").replace(",", "")
        if not cleaned:
            return Decimal(0)
        return Decimal(cleaned)

    def get_date(self, row: list[str]) -> str:
        return row[_DATE].strip()

    def get_merchant(self, row: list[str]) -> str:
        return row[_DESCRIPTION].strip()

    def get_amount(self, row: list[str]) -> str:
        # Return a canonical Decimal string so CardTransaction's round-trip
        # assertion holds even for thousands separators or a leading "$".
        return str(self._to_decimal(row[_DEBIT]))

    def should_skip(self, row: list[str]) -> bool:
        if len(row) <= _CREDIT:
            return True
        try:
            debit = self._to_decimal(row[_DEBIT])
        except InvalidOperation:
            # Header leftovers or malformed amount; drop rather than guess.
            return True
        # Credit-only rows are payments/refunds we deliberately do not import.
        return debit == 0
