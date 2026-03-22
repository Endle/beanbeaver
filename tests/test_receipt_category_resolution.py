from __future__ import annotations

from decimal import Decimal

from beanbeaver.receipt.ocr_parser.items_text_parser import _extract_items
from beanbeaver.runtime.item_category_rules import load_receipt_structuring_rule_layers


def test_text_parser_resolves_item_category_to_account() -> None:
    items = _extract_items(
        [
            "WHITE POMELO 2.68",
            "SUBTOTAL 2.68",
            "TOTAL 2.68",
        ],
        summary_amounts=set(),
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert len(items) == 1
    assert items[0].price == Decimal("2.68")
    assert items[0].category == "Expenses:Food:Grocery:Fruit"
