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


def test_extract_items_with_bbox_keeps_quantity_total_off_deposit_stub() -> None:
    lines = [
        {
            "text": "(3)06365703339 GROWERS CIDER HQ 10.47",
            "words": [
                _word("(3)06365703339", 0.074, 0.385, 0.310, 0.403),
                _word("GROWERS CIDER", 0.373, 0.381, 0.597, 0.403),
                _word("HQ", 0.657, 0.379, 0.702, 0.400),
                _word("10.47", 0.869, 0.393, 0.963, 0.415),
            ],
        },
        {
            "text": "3 @ $3.49",
            "words": [_word("3 @ $3.49", 0.102, 0.403, 0.261, 0.422)],
        },
        {
            "text": "DEPOSIT 1 0.30",
            "words": [
                _word("DEPOSIT 1", 0.100, 0.421, 0.258, 0.439),
                _word("0.30", 0.884, 0.430, 0.961, 0.452),
            ],
        },
        {
            "text": "3@$0.10 2.79",
            "words": [
                _word("3@$0.10", 0.098, 0.438, 0.225, 0.457),
                _word("2.79", 0.812, 0.451, 0.893, 0.473),
            ],
        },
        {
            "text": "06365703620 GROW CIDER HQ 0.10",
            "words": [
                _word("06365703620", 0.061, 0.457, 0.259, 0.475),
                _word("GROW CIDER", 0.322, 0.456, 0.498, 0.476),
                _word("HQ", 0.676, 0.454, 0.720, 0.475),
                _word("0.10", 0.882, 0.469, 0.964, 0.492),
            ],
        },
        {
            "text": "DEPOSIT 1",
            "words": [_word("DEPOSIT 1", 0.094, 0.475, 0.256, 0.495)],
        },
        {
            "text": "TOTAL 13.66",
            "words": [
                _word("TOTAL", 0.090, 0.500, 0.180, 0.512),
                _word("13.66", 0.880, 0.500, 0.950, 0.512),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = [(item.description, item.price) for item in items]
    assert any("GROW CIDER" in description and price == Decimal("2.79") for description, price in pairs)
    assert not any(description == "DEPOSIT 1" and price == Decimal("2.79") for description, price in pairs)


def test_extract_items_with_bbox_skips_duplicate_code_row_price_before_next_item() -> None:
    lines = [
        {
            "text": "27-PRODUCE CANTALOUPE MRJ 1.99",
            "words": [
                _word("27-PRODUCE", 0.017, 0.493, 0.205, 0.514),
                _word("CANTALOUPE", 0.318, 0.510, 0.496, 0.529),
                _word("MRJ", 0.676, 0.510, 0.738, 0.529),
                _word("1.99", 0.817, 0.507, 0.896, 0.528),
            ],
        },
        {
            "text": "4050 1.99",
            "words": [
                _word("4050", 0.055, 0.513, 0.132, 0.532),
                _word("1.99", 0.784, 0.525, 0.862, 0.547),
            ],
        },
        {
            "text": "81363501124 BLACKBERRIES 60Z MRJ",
            "words": [
                _word("81363501124", 0.054, 0.531, 0.254, 0.551),
                _word("BLACKBERRIES 60Z", 0.319, 0.528, 0.602, 0.549),
                _word("MRJ", 0.641, 0.528, 0.704, 0.547),
            ],
        },
        {
            "text": "TOTAL 3.98",
            "words": [
                _word("TOTAL", 0.090, 0.600, 0.180, 0.612),
                _word("3.98", 0.880, 0.600, 0.950, 0.612),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = [(item.description, item.price) for item in items]
    assert ("CANTALOUPE", Decimal("1.99")) in pairs
    assert not any("BLACKBERRIES" in description and price == Decimal("1.99") for description, price in pairs)


def test_extract_items_with_bbox_accepts_embedded_trailing_price_word() -> None:
    lines = [
        {
            "text": "2146010 SEAFOOD CNTR gnigoQq bn14.99",
            "words": [
                _word("2146010", 0.056, 0.568, 0.190, 0.589),
                _word("SEAFOOD CNTR", 0.320, 0.565, 0.539, 0.585),
                _word("gnigoQq bn14.99", 0.567, 0.564, 0.899, 0.586),
            ],
        },
        {
            "text": "2146010b SEAFOOD CNTR noitqQ 14.99",
            "words": [
                _word("2146010b", 0.060, 0.586, 0.233, 0.606),
                _word("SEAFOOD CNTR", 0.320, 0.584, 0.534, 0.606),
                _word("noitqQ", 0.581, 0.586, 0.706, 0.611),
                _word("14.99", 0.803, 0.584, 0.898, 0.606),
            ],
        },
        {
            "text": "TOTAL 29.98",
            "words": [
                _word("TOTAL", 0.090, 0.650, 0.180, 0.662),
                _word("29.98", 0.880, 0.650, 0.950, 0.662),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = [(item.description, item.price) for item in items]
    assert pairs.count(("SEAFOOD CNTR", Decimal("14.99"))) == 2
