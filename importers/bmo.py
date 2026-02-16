"""BMO credit card importer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import BaseCardImporter

if TYPE_CHECKING:
    from beancount.ingest.cache import _FileMemo


class BmoImporter(BaseCardImporter):
    """BMO credit cards.

    Handles multiple BMO card variants based on filename patterns.

    Example:
        importer = BmoImporter(
            account="Liabilities:CreditCard:BMO:CardA",
            porter_account="Liabilities:CreditCard:BMO:CardB",
        )
    """

    date_format = "%Y%m%d"
    use_pandas = True
    skip_rows = 2

    def __init__(
        self,
        account: str,
        porter_account: str | None = None,
        currency: str | None = None,
    ) -> None:
        super().__init__(account=account, currency=currency)
        self.porter_account = porter_account

    def identify(self, f: _FileMemo) -> bool:
        return True

    def determine_account(self, filename: str) -> str:
        fn_lower = filename.lower()
        if fn_lower == "porter.csv" and self.porter_account:
            return self.porter_account
        return self.account

    def get_date(self, row: tuple[int, Any]) -> str:
        return row[1]["Transaction Date"]

    def get_amount(self, row: tuple[int, Any]) -> str:
        return row[1]["Transaction Amount"]

    def get_merchant(self, row: tuple[int, Any]) -> str:
        return row[1]["Description"]
