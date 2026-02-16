"""PC Financial credit card importer."""

from __future__ import annotations

from .base import BaseCardImporter


class PcfImporter(BaseCardImporter):
    """PC Financial credit cards.

    Example:
        importer = PcfImporter(account="Liabilities:CreditCard:PCFinancial:CardA")
    """

    date_format = "%m/%d/%Y"
    identify_filename = "report.csv"

    def get_date(self, row: list[str]) -> str:
        return row[3]

    def get_amount(self, row: list[str]) -> str:
        return row[5].lstrip("-")

    def get_merchant(self, row: list[str]) -> str:
        return row[0]

    def should_skip(self, row: list[str]) -> bool:
        trans_type = row[1]
        if trans_type == "PAYMENT":
            return True
        return False
