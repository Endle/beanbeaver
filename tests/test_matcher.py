"""Tests for receipt-transaction matching."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

from beanbeaver.domain.receipt import Receipt, ReceiptItem
from beanbeaver.receipt.matcher import (
    MatchConfig,
    MerchantFamily,
    _merchant_similarity,
    _try_match,
    match_receipt_to_transactions,
    relaxed_candidate_match_config,
    rust_backend_loaded,
)


def make_receipt(
    merchant: str = "T&T",
    receipt_date: date = date(2024, 1, 15),
    total: Decimal = Decimal("100.00"),
) -> Receipt:
    """Helper to create test receipts."""
    return Receipt(
        merchant=merchant,
        date=receipt_date,
        total=total,
        items=[ReceiptItem(description="Test Item", price=total)],
    )


def make_transaction(
    payee: str = "T&T SUPERMARKET",
    txn_date: date = date(2024, 1, 15),
    amount: Decimal = Decimal("-100.00"),
) -> MagicMock:
    """Helper to create mock beancount transactions."""
    txn = MagicMock()
    txn.date = txn_date
    txn.payee = payee
    txn.meta = {"filename": "test.beancount", "lineno": 10}

    posting = MagicMock()
    posting.units = MagicMock()
    posting.units.number = amount
    posting.account = "Liabilities:CreditCard:CIBC"
    txn.postings = [posting]

    return txn


def merchant_families() -> tuple[MerchantFamily, ...]:
    return (
        MerchantFamily(
            canonical="REAL CANADIAN SUPERSTORE",
            aliases=("REAL CANADIAN", "RCSS"),
        ),
    )


class TestMatchReceiptToTransactions:
    """Tests for the main matching API."""

    def test_exact_match_high_confidence(self) -> None:
        receipt = make_receipt()
        txn = make_transaction()

        matches = match_receipt_to_transactions(receipt, [txn])

        assert len(matches) == 1
        assert matches[0].confidence > 0.9

    def test_no_match_wrong_amount(self) -> None:
        receipt = make_receipt(total=Decimal("100.00"))
        txn = make_transaction(amount=Decimal("-500.00"))

        matches = match_receipt_to_transactions(receipt, [txn])

        assert len(matches) == 0

    def test_no_match_wrong_date(self) -> None:
        receipt = make_receipt(receipt_date=date(2024, 1, 1))
        txn = make_transaction(txn_date=date(2024, 1, 20))

        matches = match_receipt_to_transactions(receipt, [txn])

        assert len(matches) == 0

    def test_match_within_date_tolerance(self) -> None:
        receipt = make_receipt(receipt_date=date(2024, 1, 15))
        txn = make_transaction(txn_date=date(2024, 1, 17))

        config = MatchConfig(date_tolerance_days=3)
        matches = match_receipt_to_transactions(receipt, [txn], config)

        assert len(matches) == 1
        assert 0.7 < matches[0].confidence < 0.95

    def test_match_within_amount_tolerance(self) -> None:
        receipt = make_receipt(total=Decimal("100.00"))
        txn = make_transaction(amount=Decimal("-100.50"))

        matches = match_receipt_to_transactions(receipt, [txn])

        assert len(matches) == 1

    def test_multiple_matches_sorted_by_confidence(self) -> None:
        receipt = make_receipt(
            merchant="T&T",
            receipt_date=date(2024, 1, 15),
            total=Decimal("100.00"),
        )
        txn1 = make_transaction(
            payee="T&T SUPERMARKET",
            txn_date=date(2024, 1, 15),
            amount=Decimal("-100.00"),
        )
        txn2 = make_transaction(
            payee="T&T SUPERMARKET",
            txn_date=date(2024, 1, 16),
            amount=Decimal("-100.00"),
        )

        matches = match_receipt_to_transactions(receipt, [txn2, txn1])

        assert len(matches) == 2
        assert matches[0].confidence > matches[1].confidence

    def test_no_match_different_merchant(self) -> None:
        receipt = make_receipt(merchant="T&T")
        txn = make_transaction(payee="WALMART STORE")

        matches = match_receipt_to_transactions(receipt, [txn])

        assert len(matches) == 0

    def test_relaxed_candidate_config_surfaces_near_miss(self) -> None:
        receipt = make_receipt(merchant="Market", total=Decimal("100.00"))
        txn = make_transaction(payee="Market", amount=Decimal("-107.00"))

        strict_matches = match_receipt_to_transactions(receipt, [txn])
        relaxed_matches = match_receipt_to_transactions(receipt, [txn], relaxed_candidate_match_config())

        assert strict_matches == []
        assert len(relaxed_matches) == 1


class TestMerchantSimilarity:
    """Tests for merchant name fuzzy matching."""

    def test_special_chars_in_name(self) -> None:
        score = _merchant_similarity("T&T", "T&T SUPERMARKET")
        assert score > 0.8

    def test_no_match_completely_different(self) -> None:
        score = _merchant_similarity("WALMART", "SAFEWAY")
        assert score < 0.3

    def test_common_word_match(self) -> None:
        score = _merchant_similarity("LOBLAW STORE", "LOBLAW SUPERMARKET")
        assert score > 0.3

    def test_single_word_merchant_not_removed_as_city(self) -> None:
        score = _merchant_similarity("COSTCO", "COSTCO BUSINESS CENTER")
        assert score > 0.8

    def test_family_alias_match(self) -> None:
        score = _merchant_similarity(
            "REAL CANADIAN",
            "RCSS 1077 TORONTO ON",
            merchant_families=merchant_families(),
        )
        assert score > 0.8


class TestTryMatch:
    """Tests for the internal _try_match function."""

    def test_skips_positive_amounts(self) -> None:
        receipt = make_receipt()
        txn = make_transaction()
        txn.postings[0].units.number = Decimal("100.00")

        config = MatchConfig()
        result = _try_match(receipt, txn, config)

        assert result is None

    def test_match_details_contains_info(self) -> None:
        receipt = make_receipt()
        txn = make_transaction()

        config = MatchConfig()
        result = _try_match(receipt, txn, config)

        assert result is not None
        assert "date:" in result.match_details
        assert "amount:" in result.match_details
        assert "merchant:" in result.match_details

    def test_match_details_report_family_match(self) -> None:
        receipt = make_receipt(
            merchant="REAL CANADIAN",
            receipt_date=date(2026, 1, 30),
            total=Decimal("73.63"),
        )
        txn = make_transaction(
            payee="RCSS 1077 TORONTO ON",
            txn_date=date(2026, 2, 2),
            amount=Decimal("-73.63"),
        )

        config = MatchConfig()
        result = _try_match(receipt, txn, config, merchant_families=merchant_families())

        assert result is not None
        assert "family match" in result.match_details


def test_rust_backend_loads_when_required() -> None:
    if os.environ.get("BEANBEAVER_REQUIRE_RUST_MATCHER") != "1":
        return

    assert rust_backend_loaded()
