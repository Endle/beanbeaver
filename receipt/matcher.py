"""Match receipts to existing credit card transactions in beancount ledger.

Supports bidirectional matching:
- Receipt -> Transactions: find CC transactions matching a receipt (Workflow B)
- Transaction -> Receipts: find approved receipts matching a CC transaction (Workflow A)
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

# TODO we may remove this beancount import in future
from beancount.core import data

from beanbeaver.domain.receipt import Receipt


@dataclass
class MatchResult:
    """Result of matching a receipt to a transaction."""

    transaction: data.Transaction
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


@dataclass
class ReceiptMatchResult:
    """Result of matching a transaction to a receipt (reverse matching)."""

    receipt: Receipt
    receipt_path: Path
    confidence: float  # 0.0 to 1.0
    match_details: str  # Human-readable explanation


def match_transaction_to_receipts(
    txn_date: date,
    txn_amount: Decimal,
    txn_payee: str,
    candidates: Sequence[tuple[Path, Receipt]],
    config: MatchConfig | None = None,
) -> list[ReceiptMatchResult]:
    """
    Find receipts matching a CC transaction (reverse of current flow).

    This supports Workflow A where receipts are scanned early and
    matched later during CC import.

    Args:
        txn_date: Transaction date
        txn_amount: Transaction amount (positive)
        txn_payee: Transaction payee/merchant name
        candidates: Pre-loaded receipt candidates as (path, receipt) pairs
        config: Matching configuration

    Returns:
        List of matching receipts, sorted by confidence (highest first)
    """
    if config is None:
        config = MatchConfig()

    matches = []

    for filepath, receipt in candidates:
        result = _try_match_receipt(txn_date, txn_amount, txn_payee, receipt, filepath, config)
        if result:
            matches.append(result)

    # Sort by confidence (highest first)
    matches.sort(key=lambda m: m.confidence, reverse=True)

    return matches


def _try_match_receipt(
    txn_date: date,
    txn_amount: Decimal,
    txn_payee: str,
    receipt: Receipt,
    receipt_path: Path,
    config: MatchConfig,
) -> ReceiptMatchResult | None:
    """
    Try to match a transaction to a single receipt.

    Uses the same scoring logic as _try_match but in reverse direction.
    """
    confidence = 0.0
    details = []

    # Check date (skip if receipt date is unknown)
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

    # Check amount
    amount_diff = abs(txn_amount - receipt.total)
    amount_tolerance = max(config.amount_tolerance, receipt.total * config.amount_tolerance_percent)

    if amount_diff > amount_tolerance:
        return None

    if amount_diff == Decimal("0"):
        confidence += 0.4
        details.append("amount: exact match")
    else:
        confidence += 0.4 * (1 - float(amount_diff / amount_tolerance))
        details.append(f"amount: ${amount_diff:.2f} off")

    # Check merchant name (fuzzy)
    merchant_score = _merchant_similarity(receipt.merchant, txn_payee)
    if merchant_score < 0.3:
        # Very low similarity - probably not a match
        return None

    confidence += 0.2 * merchant_score
    if merchant_score > 0.8:
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
    ledger_entries: Sequence[data.Directive],
    config: MatchConfig | None = None,
) -> list[MatchResult]:
    """
    Find transactions in pre-loaded ledger entries that match the given receipt.

    Args:
        receipt: Approved receipt data
        ledger_entries: Pre-loaded beancount entries from caller
        config: Matching configuration

    Returns:
        List of matching transactions, sorted by confidence (highest first)
    """
    if config is None:
        config = MatchConfig()

    transactions = [e for e in ledger_entries if isinstance(e, data.Transaction)]
    return match_receipt_to_transactions(receipt, transactions, config)


def match_receipt_to_transactions(
    receipt: Receipt,
    transactions: list[data.Transaction],
    config: MatchConfig | None = None,
) -> list[MatchResult]:
    """
    Find transactions that match the given receipt from a pre-loaded list.

    Args:
        receipt: Approved receipt data
        transactions: List of pre-loaded beancount transactions
        config: Matching configuration

    Returns:
        List of matching transactions, sorted by confidence (highest first)
    """
    if config is None:
        config = MatchConfig()

    matches = []

    for txn in transactions:
        result = _try_match(receipt, txn, config)
        if result:
            matches.append(result)

    # Sort by confidence (highest first)
    matches.sort(key=lambda m: m.confidence, reverse=True)

    return matches


def _try_match(
    receipt: Receipt,
    txn: data.Transaction,
    config: MatchConfig,
) -> MatchResult | None:
    """
    Try to match a receipt to a single transaction.

    Returns MatchResult if it matches, None otherwise.
    """
    confidence = 0.0
    details = []

    # Check date (skip if receipt date is unknown)
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

    # Check amount - find the credit card posting (negative amount)
    txn_amount: Decimal | None = None
    for posting in txn.postings:
        number = posting.units.number if posting.units else None
        if number is not None and number < 0:
            txn_amount = abs(number)
            break

    if txn_amount is None:
        return None

    amount_diff = abs(txn_amount - receipt.total)
    amount_tolerance = max(config.amount_tolerance, receipt.total * config.amount_tolerance_percent)

    if amount_diff > amount_tolerance:
        return None

    if amount_diff == Decimal("0"):
        confidence += 0.4
        details.append("amount: exact match")
    else:
        confidence += 0.4 * (1 - float(amount_diff / amount_tolerance))
        details.append(f"amount: ${amount_diff:.2f} off")

    # Check merchant name (fuzzy)
    merchant_score = _merchant_similarity(receipt.merchant, txn.payee or "")
    if merchant_score < 0.3:
        # Very low similarity - probably not a match
        return None

    confidence += 0.2 * merchant_score
    if merchant_score > 0.8:
        details.append("merchant: good match")
    else:
        details.append(f"merchant: partial match ({merchant_score:.0%})")

    # Get file info from metadata
    file_path = txn.meta.get("filename", "unknown")
    line_number = txn.meta.get("lineno", 0)

    return MatchResult(
        transaction=txn,
        file_path=file_path,
        line_number=line_number,
        confidence=confidence,
        match_details=", ".join(details),
    )


def _merchant_similarity(receipt_merchant: str, txn_payee: str) -> float:
    """
    Calculate similarity between receipt merchant name and transaction payee.

    Returns a score from 0.0 to 1.0.
    """

    # Normalize both strings
    def normalize(s: str) -> str:
        s = s.upper()
        # Remove common suffixes/noise
        s = re.sub(r"\s+(INC|LLC|LTD|CORP|CO|#\d+|\d+)\.?$", "", s)
        # Remove location info
        s = re.sub(r",?\s*[A-Z]{2}\s*$", "", s)  # State/province codes
        # Strip trailing city names only when they appear after a separator.
        # This avoids removing single-word merchants like "COSTCO".
        s = re.sub(r"(?:,\s*|\s+)[A-Z][A-Za-z]+\s*$", "", s)
        # Keep only alphanumeric
        s = re.sub(r"[^A-Z0-9]", "", s)
        return s

    r = normalize(receipt_merchant)
    t = normalize(txn_payee)

    if not r or not t:
        return 0.0

    # Check if one contains the other
    if r in t or t in r:
        return 0.9

    # Check common prefix
    min_len = min(len(r), len(t))
    common_prefix = 0
    for i in range(min_len):
        if r[i] == t[i]:
            common_prefix += 1
        else:
            break

    if common_prefix >= 4:  # At least 4 chars match
        return 0.5 + 0.4 * (common_prefix / min_len)

    # Check if key words match
    r_words = set(re.findall(r"[A-Z]{3,}", receipt_merchant.upper()))
    t_words = set(re.findall(r"[A-Z]{3,}", txn_payee.upper()))

    if r_words and t_words:
        common_words = r_words & t_words
        if common_words:
            return 0.3 + 0.4 * (len(common_words) / len(r_words | t_words))

    return 0.0


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
