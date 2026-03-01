"""Tests for card-transaction posting behavior."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from beanbeaver.domain.card_transaction import CardTransaction


def test_refund_preserves_detected_category() -> None:
    txn = CardTransaction(
        date=datetime(2026, 2, 5),
        transaction_amount=Decimal("-8.99"),
        raw_merchant_name="COSTCO WHOLESALE W545 MARKHAM ON",
        card_name="Liabilities:CreditCard:Dan:BMO:Porter",
    )

    rendered = txn.create_beancount_transaction(category="Expenses:Food:Grocery")

    assert rendered is not None
    assert rendered.postings[0].account == "Liabilities:CreditCard:Dan:BMO:Porter"
    assert rendered.postings[0].units.number == Decimal("8.99")
    assert rendered.postings[1].account == "Expenses:Food:Grocery"
    assert rendered.postings[1].units.number == Decimal("-8.99")


def test_small_uncategorized_purchase_uses_not_assigned_bucket() -> None:
    txn = CardTransaction(
        date=datetime(2026, 2, 2),
        transaction_amount=Decimal("4.96"),
        raw_merchant_name="UNKNOWN SHOP",
        card_name="Liabilities:CreditCard:Dan:BMO:Porter",
    )

    rendered = txn.create_beancount_transaction(category="Expenses:Uncategorized")

    assert rendered is not None
    assert rendered.postings[0].account == "Liabilities:CreditCard:Dan:BMO:Porter"
    assert rendered.postings[0].units.number == Decimal("-4.96")
    assert rendered.postings[1].account == "Expenses:Shopping:NotAssigned"
    assert rendered.postings[1].units.number == Decimal("4.96")
