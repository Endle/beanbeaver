from decimal import Decimal

from beanbeaver.receipt.ocr_parser.common import _is_section_header_text
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


def test_extract_items_keeps_priced_meat_label_as_item() -> None:
    lines = [
        "&& 03-Meat",
        "Meat 6.48",
        "&& 06-Frozen",
        "Baifu - Sweetened Soya Mi 2.59",
        "SUB Total 9.07",
        "Total after Tax 9.07",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("9.07")},
        item_category_rule_layers=load_item_category_rule_layers(),
    )

    assert any(item.description == "Meat" and item.price == Decimal("6.48") for item in items)
    assert all(item.description != "&& 06-Frozen" for item in items)


def test_section_header_with_symbol_prefix_is_detected() -> None:
    assert _is_section_header_text("&& 06-Frozen")


def test_extract_items_skips_malformed_offer_fragments_with_price() -> None:
    lines = [
        "XBL - Spicy Crawfish Past 1.98",
        "(J@6.99(1/$1.98)",
        "1 @ $1.98",
        "XBL - Spicy Crawfish Past 1.98",
        "(@6.99(1/$1.98",
        "1 @ $1.98",
        "SUB Total 3.96",
        "Total after Tax 3.96",
    ]

    items = _extract_items(
        lines,
        summary_amounts={Decimal("3.96")},
        item_category_rule_layers=load_item_category_rule_layers(),
    )

    matching = [item for item in items if item.price == Decimal("1.98")]
    assert len(matching) == 2
    assert all(item.description == "XBL - Spicy Crawfish Past" for item in matching)
