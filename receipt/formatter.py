"""Format Receipt data as beancount transactions."""

from typing import TYPE_CHECKING

from beanbeaver.domain.receipt import Receipt
from beanbeaver.receipt._rust import require_rust_matcher
from beanbeaver.receipt.item_categories import account_for_category_key

if TYPE_CHECKING:
    from .matcher import MatchResult


def _posting_account_for_item(category: str | None, *, default: str) -> str:
    """Resolve one receipt item category to a Beancount posting account."""
    return account_for_category_key(category, default=default) or default


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
