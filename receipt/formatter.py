"""Format Receipt data as beancount transactions."""

import re
from decimal import Decimal
from typing import TYPE_CHECKING

from beanbeaver.domain.receipt import Receipt, ReceiptWarning
from beanbeaver.receipt._rust import require_rust_matcher
from beanbeaver.receipt.item_categories import account_for_category_key

if TYPE_CHECKING:
    from .matcher import MatchResult


def _format_receipt_date_for_output(receipt: Receipt) -> tuple[str, bool]:
    """Format receipt date for output, returning (date_str, is_placeholder)."""
    if receipt.date_is_placeholder:
        return receipt.date.isoformat(), True
    return receipt.date.isoformat(), False


def _format_postings_aligned(
    postings: list[tuple[str, str, str | None]],
    indent: str = "  ",
) -> list[str]:
    """
    Format posting lines with aligned amounts and comments.

    Args:
        postings: List of (account, amount_with_currency, comment_or_none) tuples
        indent: Indentation prefix for each line

    Returns:
        List of formatted posting lines with aligned amounts and comments
    """
    if not postings:
        return []

    # Calculate max widths for alignment
    max_account_len = max(len(account) for account, _, _ in postings)
    max_amount_len = max(len(amount) for _, amount, _ in postings)

    # Format each line with aligned accounts, amounts, and comments
    lines = []
    for account, amount, comment in postings:
        # Pad account to max length, right-align amount
        account_padded = account.ljust(max_account_len)
        amount_padded = amount.rjust(max_amount_len)
        base = f"{indent}{account_padded}  {amount_padded}"
        if comment:
            lines.append(f"{base}  ; {comment}")
        else:
            lines.append(base)

    return lines


def _extract_card_last4(raw_text: str) -> str | None:
    """Extract a card last-4 from raw OCR text if present."""
    if not raw_text:
        return None
    for line in raw_text.split("\n"):
        if "*" not in line:
            continue
        match = re.search(r"\*{2,}\s*([0-9]{4})\b", line)
        if match:
            return match.group(1)
    return None


def _posting_account_for_item(category: str | None, *, default: str) -> str:
    """Resolve one receipt item category to a Beancount posting account."""
    return account_for_category_key(category, default=default) or default


def _build_posting_warning_map(
    warnings: list[ReceiptWarning],
    item_posting_indexes: list[int],
) -> dict[int, list[str]]:
    """Map formatted posting indexes to parser warning strings."""
    posting_warnings: dict[int, list[str]] = {}
    if not warnings:
        return posting_warnings

    for warning in warnings:
        if not warning.message:
            continue
        if item_posting_indexes:
            if warning.after_item_index is None:
                target_item_idx = len(item_posting_indexes) - 1
            else:
                target_item_idx = max(0, min(warning.after_item_index, len(item_posting_indexes) - 1))
            posting_idx = item_posting_indexes[target_item_idx]
        else:
            # No items extracted; place warning after first posting (credit card line).
            posting_idx = 0
        posting_warnings.setdefault(posting_idx, []).append(warning.message)

    return posting_warnings


def _inject_posting_warnings(
    formatted_postings: list[str],
    posting_warnings: dict[int, list[str]],
) -> list[str]:
    """Insert warning comments immediately after anchored posting lines."""
    if not posting_warnings:
        return formatted_postings

    output: list[str] = []
    for idx, posting_line in enumerate(formatted_postings):
        output.append(posting_line)
        for msg in posting_warnings.get(idx, []):
            output.append(f"; WARN:PARSER {msg}")
    return output


def format_parsed_receipt(
    receipt: Receipt,
    credit_card_account: str = "Liabilities:CreditCard:PENDING",
    image_sha256: str | None = None,
) -> str:
    """
    Format a receipt with metadata header for later parsing back.

    This format is used for Workflow A (scan early) where receipts
    are saved and later matched to CC transactions during import.

    The metadata header allows efficient reconstruction of the Receipt
    object without parsing the full beancount transaction.

    Args:
        receipt: Parsed receipt data
        credit_card_account: Placeholder credit card account

    Returns:
        Formatted beancount content with metadata header
    """
    item_accounts = [
        _posting_account_for_item(item.category, default="Expenses:FIXME")
        for item in receipt.items
    ]
    return require_rust_matcher().receipt_format_parsed_receipt(
        receipt,
        item_accounts,
        credit_card_account,
        image_sha256,
    )


def format_draft_beancount(receipt: Receipt, credit_card_account: str = "Liabilities:CreditCard:FIXME") -> str:
    """
    Format a Receipt as a draft beancount transaction.

    The output includes FIXME markers and raw OCR text for manual review.

    Args:
        receipt: Parsed receipt data
        credit_card_account: Default credit card account to use

    Returns:
        Formatted beancount transaction as a string
    """
    item_accounts = [
        _posting_account_for_item(item.category, default="Expenses:FIXME")
        for item in receipt.items
    ]
    return require_rust_matcher().receipt_format_draft_beancount(
        receipt,
        item_accounts,
        credit_card_account,
    )


def generate_filename(receipt: Receipt) -> str:
    """
    Generate a filename for the draft beancount file.

    Format: YYYY-MM-DD-merchant.beancount
    """
    return require_rust_matcher().receipt_generate_filename(receipt)


def format_enriched_transaction(
    receipt: Receipt,
    match: "MatchResult",
    default_expense: str = "Expenses:FIXME",
) -> str:
    """
    Format an enriched transaction using matched CC transaction info.

    Uses the actual date, payee, and account from the matched transaction,
    but replaces the single expense line with itemized splits from the receipt.

    Args:
        receipt: Parsed receipt data with items
        match: Matched transaction from ledger
        default_expense: Default expense category for items

    Returns:
        Formatted beancount transaction as a string
    """
    item_accounts = [
        _posting_account_for_item(item.category, default=default_expense)
        for item in receipt.items
    ]
    return require_rust_matcher().receipt_format_enriched_transaction(
        receipt,
        item_accounts,
        match,
        default_expense,
    )
