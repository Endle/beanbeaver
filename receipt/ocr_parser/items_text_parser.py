"""Text-line based receipt item extraction."""
from decimal import Decimal

from beanbeaver.domain.receipt import ReceiptItem, ReceiptWarning

from .._rust import require_rust_matcher
from ..item_categories import ItemCategoryRuleLayers, categorize_item


def _extract_items(
    lines: list[str],
    summary_amounts: set[Decimal] | None = None,
    warning_sink: list[ReceiptWarning] | None = None,
    *,
    item_category_rule_layers: ItemCategoryRuleLayers,
) -> list[ReceiptItem]:
    """
    Extract line items from receipt.

    This is heuristic-based and will likely need manual correction.
    Handles multi-line item formats where description and price are on separate lines.

    Args:
        lines: List of text lines from the receipt
        summary_amounts: Set of Decimal amounts (total, tax, subtotal) to exclude from items
    """
    if summary_amounts is None:
        summary_amounts = set()
    native_items, native_warnings = require_rust_matcher().receipt_extract_text_items(
        lines,
        [int(amount * 100) for amount in summary_amounts],
    )

    if warning_sink is not None:
        warning_sink.extend(
            ReceiptWarning(message=message, after_item_index=after_item_index)
            for message, after_item_index in native_warnings
        )

    return [
        ReceiptItem(
            description=description,
            price=Decimal(price_cents) / Decimal("100"),
            quantity=int(quantity),
            category=categorize_item(category_source, rule_layers=item_category_rule_layers),
        )
        for description, category_source, price_cents, quantity in native_items
    ]
