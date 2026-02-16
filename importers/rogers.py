"""Rogers Bank credit card importer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import BaseCardImporter

if TYPE_CHECKING:
    from beancount.ingest.cache import _FileMemo


class RogersImporter(BaseCardImporter):
    """Rogers Bank credit cards.

    Example:
        importer = RogersImporter(account="Liabilities:CreditCard:Rogers:CardA")
    """

    date_format = "%Y-%m-%d"
    use_pandas = True

    def identify(self, f: _FileMemo) -> bool:
        return True

    def get_date(self, row: tuple[int, Any]) -> str:
        return row[1]["Date"]

    def get_amount(self, row: tuple[int, Any]) -> str:
        amt = row[1]["Amount"]
        if amt[0] == "$":
            return amt.strip("$")
        return amt

    def get_merchant(self, row: tuple[int, Any]) -> str:
        return row[1]["Merchant Name"]

    def should_skip(self, row: tuple[int, Any]) -> bool:
        amt = row[1]["Amount"]
        if amt[0] == "-":
            return True
        return False
