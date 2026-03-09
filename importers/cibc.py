"""CIBC credit card importer including Simplii."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from beanbeaver.runtime import get_logger
from beancount.core import data

from .base import BaseCardImporter

if TYPE_CHECKING:
    from beancount.ingest.cache import _FileMemo

logger = get_logger(__name__)


class CibcImporter(BaseCardImporter):
    """CIBC credit cards including Simplii.

    Handles both CIBC and Simplii CSV exports.

    Example:
        importer = CibcImporter(
            account="Liabilities:CreditCard:CIBC:CardA",
            simplii_account="Liabilities:CreditCard:CIBC:CardB",
        )
    """

    date_format = "%Y-%m-%d"

    def __init__(
        self,
        account: str,
        simplii_account: str,
        currency: str | None = None,
    ) -> None:
        """Initialize CIBC importer.

        Args:
            account: Account for CIBC CSV files.
            simplii_account: Account for Simplii CSV files.
            currency: Currency code. Defaults to CAD.
        """
        super().__init__(account=account, currency=currency)
        if not simplii_account:
            raise ValueError("CibcImporter requires a valid simplii_account name")
        self.simplii_account = simplii_account

    def identify(self, f: _FileMemo) -> bool:
        return True

    def determine_account(self, filename: str) -> str:
        if "SIMPLII" in filename.upper():
            return self.simplii_account
        return self.account

    def get_date_format(self, date_str: str) -> str:
        if "/" in date_str:
            return "%m/%d/%Y"
        return "%Y-%m-%d"

    def get_date(self, row: list[str]) -> str:
        return row[0]

    def get_amount(self, row: list[str]) -> str:
        return row[2]

    def get_merchant(self, row: list[str]) -> str:
        return row[1]

    def should_skip(self, row: list[str]) -> bool:
        if row[0] == "Date":
            return True
        if row[2] == "":
            # TODO(security): Raw statement rows may include sensitive transaction data.
            # Keep only for localhost-only operation; redact before non-localhost deployment.
            logger.debug("Skipping payment row: %s", row)
            return True
        return False

    def _process_row(self, row: list[str], index: int, account: str) -> data.Transaction | None:
        from beanbeaver.domain.card_transaction import CardTransaction

        if self.should_skip(row):
            return None

        date_str = self.get_date(row)
        date_format = self.get_date_format(date_str)
        parsed_date = datetime.datetime.strptime(date_str, date_format)

        transaction = CardTransaction(
            parsed_date, self.get_amount(row), self.get_merchant(row), account, "NO NOTE", self.currency
        )

        meta = data.new_metadata("cibc", index)
        category = self.rule_engine.categorize(transaction)
        return transaction.create_beancount_transaction(meta=meta, category=category)
