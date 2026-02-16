"""Canadian Tire Financial Services credit card importer."""

from __future__ import annotations

from typing import Any

from beanbeaver.runtime import get_logger

from .base import BaseCardImporter

logger = get_logger(__name__)


class CanadianTireFinancialImporter(BaseCardImporter):
    """Canadian Tire Financial Services credit cards.

    Example:
        importer = CanadianTireFinancialImporter(account="Liabilities:CreditCard:CTFS:CardA")
    """

    date_format = "%Y-%m-%d"
    identify_filename = "Transactions.csv"
    use_pandas = True
    skip_rows = 3

    def get_date(self, row: tuple[int, Any]) -> str:
        return row[1]["TRANSACTION DATE"]

    def get_amount(self, row: tuple[int, Any]) -> str:
        return row[1]["AMOUNT"]

    def get_merchant(self, row: tuple[int, Any]) -> str:
        return row[1]["DESCRIPTION"]

    def should_skip(self, row: tuple[int, Any]) -> bool:
        merchant = row[1]["DESCRIPTION"]
        trans_type = row[1]["TYPE"]
        if trans_type == "PAYMENT" and ("PAYMENT" in merchant or "PMT" in merchant):
            # TODO(security): Raw statement rows may include sensitive transaction data.
            # Keep only for localhost-only operation; redact before non-localhost deployment.
            logger.debug("Skipping bank payment: %s", row[1])
            return True
        return False

    def identify(self, f: Any) -> bool:
        return True
