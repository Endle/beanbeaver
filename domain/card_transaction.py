"""CardTransaction dataclass for representing credit card transactions."""

import datetime
import decimal
import logging
from dataclasses import dataclass
from typing import Any

from beancount.core import amount, data, flags
from beancount.core.number import D

logger = logging.getLogger(f"beancount_local.{__name__}")


def create_simple_posting(
    expense_name: str, transaction_amount: str | decimal.Decimal, currency: str = "CAD"
) -> data.Posting:
    simple = data.Posting(expense_name, amount.Amount(D(transaction_amount), currency), None, None, None, None)
    return simple


@dataclass
class CardTransaction:
    date: datetime.datetime
    transaction_amount: float | str | decimal.Decimal
    raw_merchant_name: str
    card_name: str
    notes: str = ""
    currency: str = "CAD"

    def deduct_expense(self, category: str | None = None) -> list[data.Posting]:
        """Create expense postings for this transaction.

        The category is provided by the caller (import/orchestration layer).
        """
        assert isinstance(self.transaction_amount, decimal.Decimal)

        # Handle refunds
        if self.transaction_amount < 0.0:
            # TODO(security): Logging `self` may expose merchant/date/amount/account data.
            # Keep only for localhost-only operation; redact before non-localhost deployment.
            logger.warning("Refund not supported yet: %s", self)
            simple = data.Posting(
                "Expenses:Uncategorized",
                amount.Amount(D(self.transaction_amount), self.currency),
                None,
                None,
                None,
                None,
            )
            return [simple]

        if category is None:
            category = "Expenses:Uncategorized"

        # Handle small uncategorized transactions
        if category == "Expenses:Uncategorized" and self.transaction_amount < 5.0:
            logger.debug("Small uncategorized transaction, skipping assignment: %s", self.raw_merchant_name)
            category = "Expenses:Shopping:NotAssigned"

        simple = data.Posting(
            category, amount.Amount(D(self.transaction_amount), self.currency), None, None, None, None
        )
        return [simple]

    def is_payment(self) -> bool:
        if "TRSF FROM/DE ACCT/CPT" in self.raw_merchant_name:
            return True
        if "PAYMENT RECEIVED" in self.raw_merchant_name:
            return True
        return False

    def is_amex_offer(self) -> bool:
        """Check if this is an AMEX offer credit transaction."""
        if float(self.transaction_amount) > 0.0:
            return False
        merchant_upper = self.raw_merchant_name.upper()
        if "PRESTO FARE" in merchant_upper:
            return True
        return False

    def create_beancount_transaction(
        self,
        meta: dict[str, Any] | None = None,
        category: str | None = None,
    ) -> data.Transaction | None:
        assert isinstance(self.date, datetime.date)
        if isinstance(self.date, datetime.datetime):
            self.date = datetime.date(self.date.year, self.date.month, self.date.day)  # type: ignore[assignment]

        if isinstance(self.transaction_amount, str):
            trans_amt_str = self.transaction_amount
            self.transaction_amount = D(self.transaction_amount)
            assert str(self.transaction_amount) == trans_amt_str.strip()
        elif isinstance(self.transaction_amount, float):
            self.transaction_amount = D(str(self.transaction_amount))

        if self.is_payment():
            # TODO(security): Logging `self` may expose merchant/date/amount/account data.
            # Keep only for localhost-only operation; redact before non-localhost deployment.
            logger.debug("Skipping credit card payment: %s", self)
            return None
        if "INSTALLMENT PLAN FOR" in self.raw_merchant_name:
            # TODO(security): Logging `self` may expose merchant/date/amount/account data.
            # Keep only for localhost-only operation; redact before non-localhost deployment.
            logger.debug("Skipping installment: %s", self)
            return None

        if self.is_amex_offer():
            # TODO(security): Logging `self` may expose merchant/date/amount/account data.
            # Keep only for localhost-only operation; redact before non-localhost deployment.
            logger.debug("Skipping amex offer: %s", self)
            return None
        txn = data.Transaction(
            meta=meta or {},
            date=self.date,
            flag=flags.FLAG_OKAY,
            payee=self.raw_merchant_name,
            narration="",
            tags=frozenset(),
            links=frozenset(),
            postings=list(),
        )

        transaction_on_card = data.Posting(
            self.card_name, amount.Amount(-1 * self.transaction_amount, self.currency), None, None, None, None
        )

        expenses = self.deduct_expense(category=category)

        txn.postings.append(transaction_on_card)
        txn.postings.extend(expenses)
        return txn

    def __str__(self) -> str:
        return (
            "Card "
            + self.card_name
            + " "
            + self.date.strftime("%Y-%m-%d")
            + " at "
            + self.raw_merchant_name
            + " : "
            + str(self.transaction_amount)
        )
