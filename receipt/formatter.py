"""Format Receipt data as beancount transactions."""

from decimal import Decimal
import re
from typing import TYPE_CHECKING

from beanbeaver.domain.receipt import Receipt, ReceiptWarning

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
    lines = []

    # Metadata header for efficient parsing
    lines.append("; === PARSED RECEIPT - AWAITING CC MATCH ===")
    lines.append(f"; @merchant: {receipt.merchant}")
    date_str, date_is_placeholder = _format_receipt_date_for_output(receipt)
    if date_is_placeholder:
        lines.append("; @date: UNKNOWN")
        lines.append(f"; FIXME: unknown date (placeholder used: {date_str})")
    else:
        lines.append(f"; @date: {date_str}")
    lines.append(f"; @total: {receipt.total:.2f}")
    lines.append(f"; @items: {len(receipt.items)}")
    if receipt.tax:
        lines.append(f"; @tax: {receipt.tax:.2f}")
    if receipt.image_filename:
        lines.append(f"; @image: {receipt.image_filename}")
        lines.append(f"; @image_filename: {receipt.image_filename}")
    if image_sha256:
        lines.append(f"; @image_sha256: {image_sha256}")
    lines.append("")

    # Main transaction
    date_str, date_is_placeholder = _format_receipt_date_for_output(receipt)
    merchant_clean = receipt.merchant.replace('"', "'")
    lines.append(f'{date_str} * "{merchant_clean}" "Receipt scan"')

    # Collect all postings for aligned formatting
    postings: list[tuple[str, str, str | None]] = []

    # Credit card posting (placeholder)
    total_str = f"-{receipt.total:.2f}"
    card_last4 = _extract_card_last4(receipt.raw_text)
    card_comment = f"card ****{card_last4}" if card_last4 else None
    postings.append((credit_card_account, f"{total_str} CAD", card_comment))

    # Item postings
    items_total = Decimal("0")
    item_posting_indexes: list[int] = []

    for item in receipt.items:
        posting_idx = len(postings)
        category = item.category or "Expenses:FIXME"
        price_str = f"{item.price:.2f}"
        desc_clean = item.description.replace('"', "'")

        if item.quantity > 1:
            postings.append((category, f"{price_str} CAD", f"{desc_clean} (qty {item.quantity})"))
        else:
            postings.append((category, f"{price_str} CAD", desc_clean))
        item_posting_indexes.append(posting_idx)

        items_total += item.price

    # Tax posting if present
    if receipt.tax:
        tax_str = f"{receipt.tax:.2f}"
        postings.append(("Expenses:Tax:HST", f"{tax_str} CAD", None))
        items_total += receipt.tax

    # Balancing line for remaining amount
    if items_total != receipt.total and receipt.total > Decimal("0"):
        diff = receipt.total - items_total
        if diff > Decimal("0"):
            diff_str = f"{diff:.2f}"
            postings.append(("Expenses:FIXME", f"{diff_str} CAD", "FIXME: unaccounted amount"))

    # Format postings with aligned comments
    formatted_postings = _format_postings_aligned(postings)
    posting_warnings = _build_posting_warning_map(receipt.warnings, item_posting_indexes)
    lines.extend(_inject_posting_warnings(formatted_postings, posting_warnings))

    # Raw OCR text as comments for reference
    if receipt.raw_text:
        lines.append("")
        lines.append("; --- Raw OCR Text (for reference) ---")
        for ocr_line in receipt.raw_text.split("\n"):
            if ocr_line.strip():
                lines.append(f"; {ocr_line}")

    lines.append("")

    return "\n".join(lines)


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
    lines = []

    # Header
    lines.append("; === DRAFT - REVIEW NEEDED ===")
    lines.append(f"; Source: {receipt.image_filename}")
    lines.append("; Generated from OCR - please verify all values")
    lines.append("")

    # Main transaction
    date_str, date_is_placeholder = _format_receipt_date_for_output(receipt)
    merchant_clean = receipt.merchant.replace('"', "'")
    if date_is_placeholder:
        lines.append(f"; FIXME: unknown date (placeholder used: {date_str})")
    lines.append(f'{date_str} * "{merchant_clean}" "FIXME: add description"')

    # Collect all postings for aligned formatting
    postings: list[tuple[str, str, str | None]] = []

    # Credit card posting (negative amount)
    total_str = f"-{receipt.total:.2f}"
    card_last4 = _extract_card_last4(receipt.raw_text)
    card_comment = f"card ****{card_last4}" if card_last4 else None
    postings.append((credit_card_account, f"{total_str} CAD", card_comment))

    # Item postings
    default_expense = "Expenses:FIXME"
    items_total = Decimal("0")
    item_posting_indexes: list[int] = []

    for item in receipt.items:
        posting_idx = len(postings)
        category = item.category or default_expense
        price_str = f"{item.price:.2f}"
        desc_clean = item.description.replace('"', "'")

        # Add quantity note if > 1
        if item.quantity > 1:
            postings.append((category, f"{price_str} CAD", f"{desc_clean} (qty {item.quantity})"))
        else:
            postings.append((category, f"{price_str} CAD", desc_clean))
        item_posting_indexes.append(posting_idx)

        items_total += item.price

    # Tax posting if present
    if receipt.tax:
        tax_str = f"{receipt.tax:.2f}"
        postings.append(("Expenses:Tax:HST", f"{tax_str} CAD", None))
        items_total += receipt.tax

    # If items don't add up to total, add a balancing line
    if items_total != receipt.total and receipt.total > Decimal("0"):
        diff = receipt.total - items_total
        if diff > Decimal("0"):
            diff_str = f"{diff:.2f}"
            postings.append(("Expenses:FIXME", f"{diff_str} CAD", "FIXME: unaccounted amount"))
        elif diff < Decimal("0"):
            lines.append(f"  ; WARNING: items total ({items_total:.2f}) exceeds receipt total ({receipt.total:.2f})")

    # Format postings with aligned comments
    formatted_postings = _format_postings_aligned(postings)
    posting_warnings = _build_posting_warning_map(receipt.warnings, item_posting_indexes)
    lines.extend(_inject_posting_warnings(formatted_postings, posting_warnings))

    lines.append("")

    # Raw OCR text as comments for reference
    lines.append("; --- Raw OCR Text (for reference) ---")
    for ocr_line in receipt.raw_text.split("\n"):
        if ocr_line.strip():
            lines.append(f"; {ocr_line}")

    return "\n".join(lines)


def generate_filename(receipt: Receipt) -> str:
    """
    Generate a filename for the draft beancount file.

    Format: YYYY-MM-DD-merchant.beancount
    """
    if receipt.date_is_placeholder:
        date_str = "unknown-date"
    else:
        date_str = receipt.date.strftime("%Y-%m-%d")
    # Clean merchant name for filename
    merchant_clean = receipt.merchant.lower()
    merchant_clean = "".join(c if c.isalnum() else "-" for c in merchant_clean)
    merchant_clean = "-".join(filter(None, merchant_clean.split("-")))  # Remove consecutive dashes

    if not merchant_clean:
        merchant_clean = "unknown"

    return f"{date_str}-{merchant_clean}.beancount"


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
    txn = match.transaction
    lines = []

    # Header with match info
    lines.append("; === ENRICHED TRANSACTION - REVIEW NEEDED ===")
    lines.append(f"; Receipt: {receipt.image_filename}")
    lines.append(f"; Matched: {match.file_path}:{match.line_number}")
    lines.append(f"; Confidence: {match.confidence:.0%} ({match.match_details})")
    lines.append("")

    # Use original transaction date and payee
    date_str = txn.date.strftime("%Y-%m-%d")
    payee_clean = (txn.payee or "").replace('"', "'")
    narration = (txn.narration or "").replace('"', "'")
    lines.append(f'{date_str} * "{payee_clean}" "{narration}"')

    # Find the credit card account and amount from original transaction
    cc_account: str | None = None
    cc_amount: Decimal | None = None
    original_expense = None

    for posting in txn.postings:
        number = posting.units.number if posting.units else None
        if number is not None and number < 0:
            cc_account = posting.account
            cc_amount = number
        elif number is not None and number > 0:
            original_expense = posting.account

    # Collect all postings for aligned formatting
    postings: list[tuple[str, str, str | None]] = []

    # Credit card posting (keep original)
    if cc_account is not None and cc_amount is not None:
        postings.append((cc_account, f"{cc_amount:.2f} CAD", None))
    else:
        postings.append(("Liabilities:CreditCard:FIXME", f"-{receipt.total:.2f} CAD", None))

    # Use original expense category as base, or default
    expense_base = original_expense or default_expense

    # Item postings
    items_total = Decimal("0")
    for item in receipt.items:
        category = item.category or expense_base
        price_str = f"{item.price:.2f}"
        desc_clean = item.description.replace('"', "'")

        if item.quantity > 1:
            postings.append((category, f"{price_str} CAD", f"{desc_clean} (qty {item.quantity})"))
        else:
            postings.append((category, f"{price_str} CAD", desc_clean))

        items_total += item.price

    # Tax posting if present
    if receipt.tax:
        tax_str = f"{receipt.tax:.2f}"
        postings.append(("Expenses:Tax:HST", f"{tax_str} CAD", None))
        items_total += receipt.tax

    # Balancing line for remaining amount
    if cc_amount is not None:
        expected_total = abs(cc_amount)
    else:
        expected_total = receipt.total

    if items_total != expected_total and expected_total > Decimal("0"):
        diff = expected_total - items_total
        if diff > Decimal("0.01"):
            diff_str = f"{diff:.2f}"
            postings.append((expense_base, f"{diff_str} CAD", "remaining/unitemized"))
        elif diff < Decimal("-0.01"):
            lines.append(f"  ; WARNING: items total ({items_total:.2f}) exceeds transaction ({expected_total:.2f})")

    # Format postings with aligned comments
    lines.extend(_format_postings_aligned(postings))

    lines.append("")

    # Original transaction for reference
    lines.append("; --- Original Transaction (to be replaced) ---")
    lines.append(f'; {date_str} * "{payee_clean}" "{narration}"')
    for posting in txn.postings:
        if posting.units:
            lines.append(f";   {posting.account}  {posting.units.number:.2f} {posting.units.currency}")

    return "\n".join(lines)
