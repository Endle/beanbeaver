"""Match receipts to existing credit card transactions in beancount ledger.

Supports bidirectional matching:
- Receipt -> Transactions: find CC transactions matching a receipt (Workflow B)
- Transaction -> Receipts: find approved receipts matching a CC transaction (Workflow A)
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast

from beanbeaver.domain.receipt import Receipt

_SCALE_FACTOR = Decimal("10000")


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
    confidence: float  # 0.0 to 1.0
    match_details: str  # Human-readable explanation


@dataclass
class MatchConfig:
    """Configuration for matching algorithm."""

    date_tolerance_days: int = 3
    amount_tolerance: Decimal = Decimal("0.10")
    amount_tolerance_percent: Decimal = Decimal("0.01")  # 1%
    merchant_min_similarity: float = 0.3


@dataclass
class ReceiptMatchResult:
    """Result of matching a transaction to a receipt (reverse matching)."""

    receipt: Receipt
    receipt_path: Path
    confidence: float  # 0.0 to 1.0
    match_details: str  # Human-readable explanation


@dataclass(frozen=True)
class MerchantFamily:
    """Canonical merchant identity plus aliases."""

    canonical: str
    aliases: tuple[str, ...]


def _load_rust_matcher() -> ModuleType | None:
    for module_name in ("beanbeaver._rust_matcher", "_rust_matcher"):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue

    project_root = Path(__file__).resolve().parents[1]
    candidate_patterns = (
        project_root / "target" / "maturin",
        project_root / "target" / "debug",
    )
    for directory in candidate_patterns:
        if not directory.exists():
            continue
        for pattern in (
            "_rust_matcher*.so",
            "lib_rust_matcher*.so",
            "_rust_matcher*.pyd",
            "lib_rust_matcher*.pyd",
            "_rust_matcher*.dylib",
            "lib_rust_matcher*.dylib",
        ):
            for candidate in sorted(directory.glob(pattern)):
                spec = importlib.util.spec_from_file_location("beanbeaver._rust_matcher", candidate)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module

    return None


_rust_matcher = _load_rust_matcher()


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


def _legacy_merchant_family_payload(
    merchant_families: Sequence[MerchantFamily] | None,
) -> list[tuple[str, list[str]]]:
    return [(family.canonical, list(family.aliases)) for family in (merchant_families or ())]


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


def _normalize_merchant_py(value: str) -> str:
    normalized = value.strip().upper()
    normalized = re.sub(r"\s+(INC|LLC|LTD|CORP|CO|#\d+|\d+)\.?$", "", normalized)
    normalized = re.sub(r",?\s*[A-Z]{2}\s*$", "", normalized)
    normalized = re.sub(r"(?:,\s*|\s+)[A-Z][A-Za-z]+\s*$", "", normalized)
    normalized = re.sub(r"[^A-Z0-9]", "", normalized)
    return normalized


def _canonicalize_merchant_py(value: str) -> tuple[str, str | None]:
    return _canonicalize_merchant_with_families_py(value, merchant_families=None)


def _canonicalize_merchant_with_families_py(
    value: str,
    *,
    merchant_families: Sequence[MerchantFamily] | None,
) -> tuple[str, str | None]:
    normalized_value = _normalize_merchant_py(value)
    if not normalized_value:
        return normalized_value, None

    for family in merchant_families or ():
        aliases = (family.canonical, *family.aliases)
        for alias in aliases:
            normalized_alias = _normalize_merchant_py(alias)
            if not normalized_alias:
                continue
            if (
                normalized_value == normalized_alias
                or normalized_value in normalized_alias
                or normalized_alias in normalized_value
            ):
                return _normalize_merchant_py(family.canonical), family.canonical

    return normalized_value, None


def rust_backend_loaded() -> bool:
    """Return whether the native matcher backend is active."""
    return _rust_matcher is not None


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


def _decimal_to_scaled(value: Decimal) -> int:
    return int(value * _SCALE_FACTOR)


def _amount_tolerance(receipt_total: Decimal, config: MatchConfig) -> Decimal:
    return max(config.amount_tolerance, receipt_total * config.amount_tolerance_percent)


def _posting_amount_to_scaled(posting: PostingLike) -> int | None:
    units = posting.units
    number = units.number if units else None
    if number is None:
        return None
    return _decimal_to_scaled(number)


def _negative_posting_amount(txn: TransactionLike) -> Decimal | None:
    for posting in txn.postings:
        number = posting.units.number if posting.units else None
        if number is not None and number < 0:
            return abs(number)
    return None


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


def _match_receipt_to_transactions_rust(
    receipt: Receipt,
    transactions: Sequence[object],
    config: MatchConfig,
    merchant_families: Sequence[MerchantFamily] | None,
) -> list[tuple[int, float, str]] | None:
    if _rust_matcher is None:
        return None
    if config.merchant_min_similarity != MatchConfig().merchant_min_similarity:
        return None

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
    legacy_payload = [
        (
            cast(TransactionLike, txn).date.toordinal(),
            cast(TransactionLike, txn).payee,
            [_posting_amount_to_scaled(posting) for posting in cast(TransactionLike, txn).postings],
        )
        for txn in transactions
    ]
    try:
        return list(
            _rust_matcher.match_receipt_to_transactions(
                _receipt_payload(receipt),
                _config_payload(config),
                payload,
                _merchant_family_payload(merchant_families),
            )
        )
    except TypeError:
        try:
            return list(
                _rust_matcher.match_receipt_to_transactions(
                    receipt.date.toordinal(),
                    _decimal_to_scaled(receipt.total),
                    receipt.merchant,
                    receipt.date_is_placeholder,
                    config.date_tolerance_days,
                    _decimal_to_scaled(config.amount_tolerance),
                    _decimal_to_scaled(config.amount_tolerance_percent),
                    _decimal_to_scaled(Decimal(str(config.merchant_min_similarity))),
                    legacy_payload,
                    _legacy_merchant_family_payload(merchant_families),
                )
            )
        except TypeError:
            return list(
                _rust_matcher.match_receipt_to_transactions(
                    receipt.date.toordinal(),
                    _decimal_to_scaled(receipt.total),
                    receipt.merchant,
                    receipt.date_is_placeholder,
                    config.date_tolerance_days,
                    _decimal_to_scaled(config.amount_tolerance),
                    _decimal_to_scaled(config.amount_tolerance_percent),
                    legacy_payload,
                    _legacy_merchant_family_payload(merchant_families),
                )
            )


def _match_transaction_to_receipts_rust(
    txn_date: date,
    txn_amount: Decimal,
    txn_payee: str,
    candidates: Sequence[tuple[Path, Receipt]],
    config: MatchConfig,
    merchant_families: Sequence[MerchantFamily] | None,
) -> list[tuple[int, float, str]] | None:
    if _rust_matcher is None:
        return None
    if config.merchant_min_similarity != MatchConfig().merchant_min_similarity:
        return None

    payload = [
        {
            "date_ordinal": receipt.date.toordinal(),
            "total_scaled": _decimal_to_scaled(receipt.total),
            "merchant": receipt.merchant,
            "date_is_placeholder": receipt.date_is_placeholder,
        }
        for _, receipt in candidates
    ]
    legacy_payload = [
        (
            receipt.date.toordinal(),
            _decimal_to_scaled(receipt.total),
            receipt.merchant,
            receipt.date_is_placeholder,
        )
        for _, receipt in candidates
    ]
    try:
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
    except TypeError:
        try:
            return list(
                _rust_matcher.match_transaction_to_receipts(
                    txn_date.toordinal(),
                    _decimal_to_scaled(txn_amount),
                    txn_payee,
                    config.date_tolerance_days,
                    _decimal_to_scaled(config.amount_tolerance),
                    _decimal_to_scaled(config.amount_tolerance_percent),
                    _decimal_to_scaled(Decimal(str(config.merchant_min_similarity))),
                    legacy_payload,
                    _legacy_merchant_family_payload(merchant_families),
                )
            )
        except TypeError:
            return list(
                _rust_matcher.match_transaction_to_receipts(
                    txn_date.toordinal(),
                    _decimal_to_scaled(txn_amount),
                    txn_payee,
                    config.date_tolerance_days,
                    _decimal_to_scaled(config.amount_tolerance),
                    _decimal_to_scaled(config.amount_tolerance_percent),
                    legacy_payload,
                    _legacy_merchant_family_payload(merchant_families),
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
    """
    Find receipts matching a CC transaction (reverse of current flow).

    This supports Workflow A where receipts are scanned early and
    matched later during CC import.
    """
    resolved_config = _config_or_default(config)
    rust_matches = _match_transaction_to_receipts_rust(
        txn_date,
        txn_amount,
        txn_payee,
        candidates,
        resolved_config,
        merchant_families,
    )
    if rust_matches is not None:
        return [
            ReceiptMatchResult(
                receipt=candidates[index][1],
                receipt_path=candidates[index][0],
                confidence=confidence,
                match_details=details,
            )
            for index, confidence, details in rust_matches
        ]

    matches: list[ReceiptMatchResult] = []
    for filepath, receipt in candidates:
        result = _try_match_receipt_py(
            txn_date,
            txn_amount,
            txn_payee,
            receipt,
            filepath,
            resolved_config,
            merchant_families=merchant_families,
        )
        if result:
            matches.append(result)
    matches.sort(key=lambda m: m.confidence, reverse=True)
    return matches


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
    if rust_matches is not None:
        if not rust_matches:
            return None
        _, confidence, details = rust_matches[0]
        return ReceiptMatchResult(
            receipt=receipt,
            receipt_path=receipt_path,
            confidence=confidence,
            match_details=details,
        )
    return _try_match_receipt_py(
        txn_date,
        txn_amount,
        txn_payee,
        receipt,
        receipt_path,
        config,
        merchant_families=merchant_families,
    )


def _try_match_receipt_py(
    txn_date: date,
    txn_amount: Decimal,
    txn_payee: str,
    receipt: Receipt,
    receipt_path: Path,
    config: MatchConfig,
    *,
    merchant_families: Sequence[MerchantFamily] | None = None,
) -> ReceiptMatchResult | None:
    confidence = 0.0
    details: list[str] = []

    if receipt.date_is_placeholder:
        details.append("date: unknown")
    else:
        date_diff = abs((txn_date - receipt.date).days)
        if date_diff > config.date_tolerance_days:
            return None
        if date_diff == 0:
            confidence += 0.4
            details.append("date: exact match")
        else:
            confidence += 0.4 * (1 - date_diff / (config.date_tolerance_days + 1))
            details.append(f"date: {date_diff} day(s) off")

    amount_diff = abs(txn_amount - receipt.total)
    amount_tolerance = _amount_tolerance(receipt.total, config)
    if amount_diff > amount_tolerance:
        return None
    if amount_diff == Decimal("0"):
        confidence += 0.4
        details.append("amount: exact match")
    else:
        confidence += 0.4 * (1 - float(amount_diff / amount_tolerance))
        details.append(f"amount: ${amount_diff:.2f} off")

    merchant_score, merchant_family = _merchant_similarity_info_py(
        receipt.merchant,
        txn_payee,
        merchant_families=merchant_families,
    )
    if merchant_score < config.merchant_min_similarity:
        return None

    confidence += 0.2 * merchant_score
    if merchant_family is not None:
        details.append(f"merchant: family match ({merchant_family})")
    elif merchant_score > 0.8:
        details.append("merchant: good match")
    else:
        details.append(f"merchant: partial match ({merchant_score:.0%})")

    return ReceiptMatchResult(
        receipt=receipt,
        receipt_path=receipt_path,
        confidence=confidence,
        match_details=", ".join(details),
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
    if rust_matches is not None:
        results: list[MatchResult] = []
        for index, confidence, details in rust_matches:
            matched_txn = cast(TransactionLike, transactions[index])
            file_path, line_number = _transaction_location(matched_txn)
            results.append(
                MatchResult(
                    transaction=matched_txn,
                    file_path=file_path,
                    line_number=line_number,
                    confidence=confidence,
                    match_details=details,
                )
            )
        return results

    matches: list[MatchResult] = []
    for candidate_txn in transactions:
        result = _try_match_py(
            receipt,
            cast(TransactionLike, candidate_txn),
            resolved_config,
            merchant_families=merchant_families,
        )
        if result:
            matches.append(result)
    matches.sort(key=lambda m: m.confidence, reverse=True)
    return matches


def _try_match(
    receipt: Receipt,
    txn: TransactionLike,
    config: MatchConfig,
    merchant_families: Sequence[MerchantFamily] | None = None,
) -> MatchResult | None:
    rust_matches = _match_receipt_to_transactions_rust(receipt, [txn], config, merchant_families)
    if rust_matches is not None:
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
    return _try_match_py(receipt, txn, config, merchant_families=merchant_families)


def _try_match_py(
    receipt: Receipt,
    txn: TransactionLike,
    config: MatchConfig,
    *,
    merchant_families: Sequence[MerchantFamily] | None = None,
) -> MatchResult | None:
    confidence = 0.0
    details: list[str] = []

    if receipt.date_is_placeholder:
        details.append("date: unknown")
    else:
        date_diff = abs((txn.date - receipt.date).days)
        if date_diff > config.date_tolerance_days:
            return None
        if date_diff == 0:
            confidence += 0.4
            details.append("date: exact match")
        else:
            confidence += 0.4 * (1 - date_diff / (config.date_tolerance_days + 1))
            details.append(f"date: {date_diff} day(s) off")

    txn_amount = _negative_posting_amount(txn)
    if txn_amount is None:
        return None

    amount_diff = abs(txn_amount - receipt.total)
    amount_tolerance = _amount_tolerance(receipt.total, config)
    if amount_diff > amount_tolerance:
        return None
    if amount_diff == Decimal("0"):
        confidence += 0.4
        details.append("amount: exact match")
    else:
        confidence += 0.4 * (1 - float(amount_diff / amount_tolerance))
        details.append(f"amount: ${amount_diff:.2f} off")

    merchant_score, merchant_family = _merchant_similarity_info_py(
        receipt.merchant,
        txn.payee or "",
        merchant_families=merchant_families,
    )
    if merchant_score < config.merchant_min_similarity:
        return None

    confidence += 0.2 * merchant_score
    if merchant_family is not None:
        details.append(f"merchant: family match ({merchant_family})")
    elif merchant_score > 0.8:
        details.append("merchant: good match")
    else:
        details.append(f"merchant: partial match ({merchant_score:.0%})")

    file_path, line_number = _transaction_location(txn)
    return MatchResult(
        transaction=txn,
        file_path=file_path,
        line_number=line_number,
        confidence=confidence,
        match_details=", ".join(details),
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
    if _rust_matcher is not None:
        try:
            return float(
                _rust_matcher.merchant_similarity(
                    receipt_merchant,
                    txn_payee,
                    _merchant_family_payload(merchant_families),
                )
            )
        except TypeError:
            return float(
                _rust_matcher.merchant_similarity(
                    receipt_merchant,
                    txn_payee,
                    _legacy_merchant_family_payload(merchant_families),
                )
            )
    return _merchant_similarity_info_py(
        receipt_merchant,
        txn_payee,
        merchant_families=merchant_families,
    )[0]


def _merchant_similarity_info_py(
    receipt_merchant: str,
    txn_payee: str,
    *,
    merchant_families: Sequence[MerchantFamily] | None = None,
) -> tuple[float, str | None]:
    normalized_receipt, receipt_family = _canonicalize_merchant_with_families_py(
        receipt_merchant,
        merchant_families=merchant_families,
    )
    normalized_txn, txn_family = _canonicalize_merchant_with_families_py(
        txn_payee,
        merchant_families=merchant_families,
    )
    if not normalized_receipt or not normalized_txn:
        return 0.0, None

    if normalized_receipt == normalized_txn and (receipt_family is not None or txn_family is not None):
        return 1.0, receipt_family or txn_family

    if normalized_receipt in normalized_txn or normalized_txn in normalized_receipt:
        return 0.9, None

    min_len = min(len(normalized_receipt), len(normalized_txn))
    common_prefix = 0
    for i in range(min_len):
        if normalized_receipt[i] == normalized_txn[i]:
            common_prefix += 1
        else:
            break
    if common_prefix >= 4:
        return 0.5 + 0.4 * (common_prefix / min_len), None

    receipt_words = set(re.findall(r"[A-Z]{3,}", receipt_merchant.upper()))
    txn_words = set(re.findall(r"[A-Z]{3,}", txn_payee.upper()))
    if receipt_words and txn_words:
        common_words = receipt_words & txn_words
        if common_words:
            return 0.3 + 0.4 * (len(common_words) / len(receipt_words | txn_words)), None
    return 0.0, None


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
