from decimal import Decimal

from beanbeaver.receipt.ocr_parser.items_text_parser import _extract_items
from beanbeaver.runtime.item_category_rules import load_item_category_rule_layers


def test_extract_items_supports_trailing_j_tax_marker() -> None:
    lines = [
        "CRLSH ZER0 0 056000010660 $8.28 J",
        "LYSOL BATH P 059631882930 $3.97 J",
        "SUBTOTAL $12.25",
        "TOTAL $12.25",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("12.25")},
        item_category_rule_layers=load_item_category_rule_layers(),
    )

    assert [item.price for item in items] == [Decimal("8.28"), Decimal("3.97")]
    assert "CRLSH ZER0" in items[0].description
    assert "LYSOL BATH" in items[1].description
