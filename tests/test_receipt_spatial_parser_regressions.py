from decimal import Decimal

from beanbeaver.receipt.receipt_structuring.parsers.items_spatial_parser import _extract_items_with_bbox
from beanbeaver.runtime.item_category_rules import load_receipt_structuring_rule_layers


def _word(text: str, x_left: float, y_top: float, x_right: float, y_bottom: float) -> dict:
    return {
        "text": text,
        "bbox": {
            "left": x_left,
            "top": y_top,
            "right": x_right,
            "bottom": y_bottom,
        },
        "confidence": 0.99,
    }


def test_extract_items_with_bbox_keeps_next_priced_item_from_stealing_code_only_price_row() -> None:
    lines = [
        {
            "text": "26-L.IQUOR COORS LIGHT 6 PK HQ 15.79",
            "words": [
                _word("26-L.IQUOR", 0.031, 0.289, 0.191, 0.309),
                _word("COORS LIGHT 6 PK HQ", 0.318, 0.301, 0.688, 0.328),
                _word("15.79", 0.760, 0.298, 0.860, 0.324),
            ],
        },
        {
            "text": "05632700795 0.60",
            "words": [
                _word("05632700795", 0.068, 0.309, 0.257, 0.328),
                _word("0.60", 0.881, 0.317, 0.963, 0.340),
            ],
        },
        {
            "text": "DEPOSIT 1 COORS PINEAPPLE HQ 3.19",
            "words": [
                _word("DEPOSIT 1", 0.102, 0.328, 0.258, 0.348),
                _word("COORS PINEAPPLE", 0.322, 0.342, 0.579, 0.365),
                _word("HQ", 0.657, 0.341, 0.701, 0.362),
                _word("3.19", 0.793, 0.338, 0.876, 0.361),
            ],
        },
        {
            "text": "05632702339 0.10",
            "words": [
                _word("05632702339", 0.070, 0.347, 0.259, 0.366),
                _word("0.10", 0.882, 0.356, 0.963, 0.377),
            ],
        },
        {
            "text": "DEPOSIT 1",
            "words": [_word("DEPOSIT 1", 0.103, 0.366, 0.260, 0.385)],
        },
        {
            "text": "TOTAL 19.68",
            "words": [
                _word("TOTAL", 0.090, 0.500, 0.180, 0.512),
                _word("19.68", 0.880, 0.500, 0.950, 0.512),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = [(item.description, item.price) for item in items]
    assert ("26-L.IQUOR COORS LIGHT 6 PK HQ", Decimal("15.79")) in pairs
    assert any("COORS PINEAPPLE" in description and price == Decimal("3.19") for description, price in pairs)
    assert not any("COORS PINEAPPLE" in description and price == Decimal("0.60") for description, price in pairs)
