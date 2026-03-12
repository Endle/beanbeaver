"""Match receipts to existing credit card transactions in beancount ledger."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, cast

from beanbeaver.domain.receipt import Receipt

from ._rust import require_rust_matcher

_SCALE_FACTOR = Decimal("10000")
_rust_matcher = require_rust_matcher()


class AmountLike(Protocol):
    number: Decimal
    currency: str


class PostingLike(Protocol):
    account: str
    units: AmountLike | None


class TransactionLike(Protocol):
    date: date
    payee: str | None
    narration: str | None
    postings: Sequence[PostingLike]


@dataclass
class MatchResult:
    """Result of matching a receipt to a transaction."""

    transaction: TransactionLike
    file_path: str
    line_number: int
    confidence: float
    match_details: str


@dataclass
class MatchConfig:
    """Configuration for matching algorithm."""

    date_tolerance_days: int = 3
    amount_tolerance: Decimal = Decimal("0.10")
    amount_tolerance_percent: Decimal = Decimal("0.01")
    merchant_min_similarity: float = 0.3


@dataclass
class ReceiptMatchResult:
    """Result of matching a transaction to a receipt."""

    receipt: Receipt
    receipt_path: Path
    confidence: float
    match_details: str


@dataclass(frozen=True)
class MerchantFamily:
    """Canonical merchant identity plus aliases."""

    canonical: str
    aliases: tuple[str, ...]


def _merchant_family_payload(
    merchant_families: Sequence[MerchantFamily] | None,
) -> list[dict[str, object]]:
    return [
        {
            "canonical": family.canonical,
            "aliases": list(family.aliases),
        }
        for family in (merchant_families or ())
    ]


def _decimal_to_scaled(value: Decimal) -> int:
    return int(value * _SCALE_FACTOR)


def _config_payload(config: MatchConfig) -> dict[str, int]:
    return {
        "date_tolerance_days": config.date_tolerance_days,
        "amount_tolerance_scaled": _decimal_to_scaled(config.amount_tolerance),
        "amount_tolerance_percent_scaled": _decimal_to_scaled(config.amount_tolerance_percent),
        "merchant_min_similarity_scaled": _decimal_to_scaled(Decimal(str(config.merchant_min_similarity))),
    }


def _receipt_payload(receipt: Receipt) -> dict[str, object]:
    return {
        "date_ordinal": receipt.date.toordinal(),
        "total_scaled": _decimal_to_scaled(receipt.total),
        "merchant": receipt.merchant,
        "date_is_placeholder": receipt.date_is_placeholder,
    }


def _posting_amount_to_scaled(posting: PostingLike) -> int | None:
    units = posting.units
    number = units.number if units else None
    if number is None:
        return None
    return _decimal_to_scaled(number)


def _transaction_location(txn: object) -> tuple[str, int]:
    file_path = str(getattr(txn, "file_path", "unknown"))
    raw_line_number = getattr(txn, "line_number", 0)
    try:
        line_number = int(raw_line_number)
    except (TypeError, ValueError):
        line_number = 0
    if file_path == "unknown" and line_number == 0:
        meta = getattr(txn, "meta", {})
        if isinstance(meta, dict):
            file_path = str(meta.get("filename", "unknown"))
            raw_lineno: Any = meta.get("lineno", 0)
            try:
                line_number = int(raw_lineno)
            except (TypeError, ValueError):
                line_number = 0
    return file_path, line_number


def rust_backend_loaded() -> bool:
    """Return whether the required native matcher backend is active."""
    return True


def _config_or_default(config: MatchConfig | None) -> MatchConfig:
    return config if config is not None else MatchConfig()


def relaxed_candidate_match_config(config: MatchConfig | None = None) -> MatchConfig:
    """Return a looser config used only for manual-review candidate fallback."""
    resolved = _config_or_default(config)
    return MatchConfig(
        date_tolerance_days=max(resolved.date_tolerance_days, 7),
        amount_tolerance=max(resolved.amount_tolerance, Decimal("2.00")),
        amount_tolerance_percent=max(resolved.amount_tolerance_percent, Decimal("0.08")),
        merchant_min_similarity=min(resolved.merchant_min_similarity, 0.15),
    )


def _match_receipt_to_transactions_rust(
    receipt: Receipt,
    transactions: Sequence[object],
    config: MatchConfig,
    merchant_families: Sequence[MerchantFamily] | None,
) -> list[tuple[int, float, str]]:
    payload = [
        {
            "date_ordinal": cast(TransactionLike, txn).date.toordinal(),
            "payee": cast(TransactionLike, txn).payee,
            "posting_amounts_scaled": [
                _posting_amount_to_scaled(posting) for posting in cast(TransactionLike, txn).postings
            ],
        }
        for txn in transactions
    ]
    return list(
        _rust_matcher.match_receipt_to_transactions(
            _receipt_payload(receipt),
            _config_payload(config),
            payload,
            _merchant_family_payload(merchant_families),
        )
    )


def _match_transaction_to_receipts_rust(
    txn_date: date,
    txn_amount: Decimal,
    txn_payee: str,
    candidates: Sequence[tuple[Path, Receipt]],
    config: MatchConfig,
    merchant_families: Sequence[MerchantFamily] | None,
) -> list[tuple[int, float, str]]:
    payload = [
        {
            "date_ordinal": receipt.date.toordinal(),
            "total_scaled": _decimal_to_scaled(receipt.total),
            "merchant": receipt.merchant,
            "date_is_placeholder": receipt.date_is_placeholder,
        }
        for _, receipt in candidates
    ]
    return list(
        _rust_matcher.match_transaction_to_receipts(
            {
                "date_ordinal": txn_date.toordinal(),
                "amount_scaled": _decimal_to_scaled(txn_amount),
                "payee": txn_payee,
            },
            _config_payload(config),
            payload,
            _merchant_family_payload(merchant_families),
        )
    )


def match_transaction_to_receipts(
    txn_date: date,
    txn_amount: Decimal,
    txn_payee: str,
    candidates: Sequence[tuple[Path, Receipt]],
    config: MatchConfig | None = None,
    merchant_families: Sequence[MerchantFamily] | None = None,
) -> list[ReceiptMatchResult]:
    """Find receipts matching a CC transaction."""
    resolved_config = _config_or_default(config)
    rust_matches = _match_transaction_to_receipts_rust(
        txn_date,
        txn_amount,
        txn_payee,
        candidates,
        resolved_config,
        merchant_families,
    )
    return [
        ReceiptMatchResult(
            receipt=candidates[index][1],
            receipt_path=candidates[index][0],
            confidence=confidence,
            match_details=details,
        )
        for index, confidence, details in rust_matches
    ]


def _try_match_receipt(
    txn_date: date,
    txn_amount: Decimal,
    txn_payee: str,
    receipt: Receipt,
    receipt_path: Path,
    config: MatchConfig,
    merchant_families: Sequence[MerchantFamily] | None = None,
) -> ReceiptMatchResult | None:
    rust_matches = _match_transaction_to_receipts_rust(
        txn_date,
        txn_amount,
        txn_payee,
        [(receipt_path, receipt)],
        config,
        merchant_families,
    )
    if not rust_matches:
        return None
    _, confidence, details = rust_matches[0]
    return ReceiptMatchResult(
        receipt=receipt,
        receipt_path=receipt_path,
        confidence=confidence,
        match_details=details,
    )


def find_matching_transactions(
    receipt: Receipt,
    ledger_entries: Sequence[object],
    config: MatchConfig | None = None,
) -> list[MatchResult]:
    """Find transactions in pre-loaded ledger entries that match the given receipt."""
    return match_receipt_to_transactions(receipt, list(ledger_entries), config)


def match_receipt_to_transactions(
    receipt: Receipt,
    transactions: Sequence[object],
    config: MatchConfig | None = None,
    merchant_families: Sequence[MerchantFamily] | None = None,
) -> list[MatchResult]:
    """Find transactions that match the given receipt from a pre-loaded list."""
    resolved_config = _config_or_default(config)
    rust_matches = _match_receipt_to_transactions_rust(
        receipt,
        transactions,
        resolved_config,
        merchant_families,
    )
    results: list[MatchResult] = []
    for index, confidence, details in rust_matches:
        txn = cast(TransactionLike, transactions[index])
        file_path, line_number = _transaction_location(txn)
        results.append(
            MatchResult(
                transaction=txn,
                file_path=file_path,
                line_number=line_number,
                confidence=confidence,
                match_details=details,
            )
        )
    return results


def _try_match(
    receipt: Receipt,
    txn: TransactionLike,
    config: MatchConfig,
    merchant_families: Sequence[MerchantFamily] | None = None,
) -> MatchResult | None:
    rust_matches = _match_receipt_to_transactions_rust(receipt, [txn], config, merchant_families)
    if not rust_matches:
        return None
    _, confidence, details = rust_matches[0]
    file_path, line_number = _transaction_location(txn)
    return MatchResult(
        transaction=txn,
        file_path=file_path,
        line_number=line_number,
        confidence=confidence,
        match_details=details,
    )


def _merchant_similarity(
    receipt_merchant: str,
    txn_payee: str,
    merchant_families: Sequence[MerchantFamily] | None = None,
) -> float:
    """
    Calculate similarity between receipt merchant name and transaction payee.

    Returns a score from 0.0 to 1.0.
    """
    return float(
        _rust_matcher.merchant_similarity(
            receipt_merchant,
            txn_payee,
            _merchant_family_payload(merchant_families),
        )
    )


def format_match_for_display(match: MatchResult) -> str:
    """Format a match result for display to user."""
    txn = match.transaction
    amount: Decimal = Decimal("0")
    account = None

    for posting in txn.postings:
        number = posting.units.number if posting.units else None
        if number is not None and number < 0:
            amount = abs(number)
            account = posting.account
            break

    return f"""Match found ({match.confidence:.0%} confidence):
  File: {match.file_path}:{match.line_number}
  Date: {txn.date}
  Payee: {txn.payee}
  Amount: ${amount:.2f}
  Account: {account}
  Details: {match.match_details}
"""


def format_receipt_match_for_display(match: ReceiptMatchResult) -> str:
    """Format a receipt match result for display to user."""
    receipt = match.receipt
    date_str = receipt.date.isoformat() if not receipt.date_is_placeholder else "UNKNOWN"
    return f"""Receipt match ({match.confidence:.0%} confidence):
  File: {match.receipt_path.name}
  Merchant: {receipt.merchant}
  Date: {date_str}
  Total: ${receipt.total:.2f}
  Items: {len(receipt.items)}
  Details: {match.match_details}
"""
