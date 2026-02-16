"""MBNA credit card importer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from beancount.core.number import D

from .base import BaseCardImporter

if TYPE_CHECKING:
    from beancount.ingest.cache import _FileMemo


class MbnaImporter(BaseCardImporter):
    """MBNA credit cards.

    Example:
        importer = MbnaImporter(account="Liabilities:CreditCard:MBNA:CardA")
    """

    date_format = "%m/%d/%Y"
    default_currency = "CAD"
    encoding = "iso-8859-1"

    def identify(self, f: _FileMemo) -> bool:
        return True

    def get_date(self, row: list[str]) -> str:
        return row[0]

    def get_amount(self, row: list[str]) -> str:
        return row[3]

    def get_merchant(self, row: list[str]) -> str:
        return row[1]

    def transform_amount(self, amt: str) -> Any:
        return -D(amt)
