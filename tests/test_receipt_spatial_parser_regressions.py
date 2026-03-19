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


def test_extract_items_with_bbox_assigns_duplicate_code_row_price_to_next_unpriced_item() -> None:
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
    assert ("BLACKBERRIES 60Z", Decimal("1.99")) in pairs


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


def test_extract_items_with_bbox_handles_actual_nofrills_coors_and_deposit_cluster() -> None:
    lines = [
        {
            "text": "26-L.IQUOR COORS LIGHT 6 PK HQ 15.79",
            "words": [
                _word("26-L.IQUOR", 0.031438127090301006, 0.28933333333333333, 0.19130434782608696, 0.30933333333333335),
                _word("COORS LIGHT 6 PK HQ", 0.3177257525083612, 0.301, 0.6876254180602007, 0.32766666666666666),
                _word("15.79", 0.7598662207357859, 0.29833333333333334, 0.8595317725752508, 0.3243333333333333),
            ],
        },
        {
            "text": "05632700795 0.60",
            "words": [
                _word("05632700795", 0.06755852842809365, 0.309, 0.2568561872909699, 0.32766666666666666),
                _word("0.60", 0.8809364548494983, 0.317, 0.9625418060200669, 0.34),
            ],
        },
        {
            "text": "DEPOSIT 1 COORS PINEAPPLE HQ 3.19",
            "words": [
                _word("DEPOSIT 1", 0.10167224080267559, 0.32766666666666666, 0.25752508361204013, 0.3476666666666667),
                _word("COORS PINEAPPLE", 0.32240802675585284, 0.3423333333333333, 0.5792642140468227, 0.36533333333333334),
                _word("HQ", 0.65685618729097, 0.341, 0.7010033444816054, 0.362),
                _word("3.19", 0.7926421404682275, 0.33766666666666667, 0.8762541806020067, 0.3606666666666667),
            ],
        },
        {
            "text": "05632702339 0.10",
            "words": [
                _word("05632702339", 0.07023411371237458, 0.3473333333333333, 0.2588628762541806, 0.366),
                _word("0.10", 0.8822742474916387, 0.3556666666666667, 0.9625418060200669, 0.37733333333333335),
            ],
        },
        {
            "text": "DEPOSIT 1",
            "words": [_word("DEPOSIT 1", 0.10301003344481606, 0.366, 0.2602006688963211, 0.38533333333333336)],
        },
        {
            "text": "TOTAL 19.68",
            "words": [
                _word("TOTAL", 0.09, 0.50, 0.18, 0.512),
                _word("19.68", 0.88, 0.50, 0.95, 0.512),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = [(item.description, item.price) for item in items]
    assert ("26-L.IQUOR COORS LIGHT 6 PK HQ", Decimal("15.79")) in pairs
    assert ("26-L.IQUOR COORS LIGHT 6 PK HQ", Decimal("0.60")) not in pairs
    assert ("DEPOSIT 1 COORS PINEAPPLE", Decimal("3.19")) in pairs
    assert ("DEPOSIT 1 COORS PINEAPPLE", Decimal("0.10")) not in pairs
    assert ("DEPOSIT 1", Decimal("0.10")) in pairs


def test_extract_items_with_bbox_handles_actual_nofrills_grocery_preceding_coors_cluster() -> None:
    lines = [
        {
            "text": "21-GROCERY",
            "words": [_word("21-GROCERY", 0.01471571906354515, 0.25466666666666665, 0.20468227424749163, 0.277)],
        },
        {
            "text": "(4)06780000235 DICED TOMATO MRJ 7.16",
            "words": [
                _word("(4)06780000235", 0.05819397993311037, 0.27466666666666667, 0.3076923076923077, 0.293),
                _word("DICED TOMATO", 0.3183946488294314, 0.2693333333333333, 0.49765886287625416, 0.2946666666666667),
                _word("MRJ", 0.6394648829431438, 0.2703333333333333, 0.7020066889632107, 0.292),
                _word("7.16", 0.8267558528428093, 0.2816666666666667, 0.9043478260869565, 0.304),
            ],
        },
        {
            "text": "4 @ $1.79",
            "words": [_word("4 @ $1.79", 0.1020066889632107, 0.2926666666666667, 0.2608695652173913, 0.31133333333333335)],
        },
        {
            "text": "26-L.IQUOR COORS LIGHT 6 PK HQ 15.79",
            "words": [
                _word("26-L.IQUOR", 0.031438127090301006, 0.28933333333333333, 0.19130434782608696, 0.30933333333333335),
                _word("COORS LIGHT 6 PK HQ", 0.3177257525083612, 0.301, 0.6876254180602007, 0.32766666666666666),
                _word("15.79", 0.7598662207357859, 0.29833333333333334, 0.8595317725752508, 0.3243333333333333),
            ],
        },
        {
            "text": "05632700795 0.60",
            "words": [
                _word("05632700795", 0.06755852842809365, 0.309, 0.2568561872909699, 0.32766666666666666),
                _word("0.60", 0.8809364548494983, 0.317, 0.9625418060200669, 0.34),
            ],
        },
        {
            "text": "DEPOSIT 1 COORS PINEAPPLE HQ 3.19",
            "words": [
                _word("DEPOSIT 1", 0.10167224080267559, 0.32766666666666666, 0.25752508361204013, 0.3476666666666667),
                _word("COORS PINEAPPLE", 0.32240802675585284, 0.3423333333333333, 0.5792642140468227, 0.36533333333333334),
                _word("HQ", 0.65685618729097, 0.341, 0.7010033444816054, 0.362),
                _word("3.19", 0.7926421404682275, 0.33766666666666667, 0.8762541806020067, 0.3606666666666667),
            ],
        },
        {
            "text": "05632702339 0.10",
            "words": [
                _word("05632702339", 0.07023411371237458, 0.3473333333333333, 0.2588628762541806, 0.366),
                _word("0.10", 0.8822742474916387, 0.3556666666666667, 0.9625418060200669, 0.37733333333333335),
            ],
        },
        {
            "text": "DEPOSIT 1",
            "words": [_word("DEPOSIT 1", 0.10301003344481606, 0.366, 0.2602006688963211, 0.38533333333333336)],
        },
        {
            "text": "TOTAL 26.84",
            "words": [
                _word("TOTAL", 0.09, 0.50, 0.18, 0.512),
                _word("26.84", 0.88, 0.50, 0.95, 0.512),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = [(item.description, item.price) for item in items]
    assert ("DICED TOMATO", Decimal("7.16")) in pairs
    assert ("26-L.IQUOR COORS LIGHT 6 PK HQ", Decimal("15.79")) in pairs
    assert ("26-L.IQUOR COORS LIGHT 6 PK HQ", Decimal("0.60")) not in pairs
    assert ("DEPOSIT 1 COORS PINEAPPLE", Decimal("3.19")) in pairs
    assert ("DEPOSIT 1", Decimal("0.10")) in pairs


def test_extract_items_with_bbox_handles_actual_nofrills_produce_and_seafood_cluster() -> None:
    lines = [
        {
            "text": "27-PRODUCE CANTALOUPE MRJ 1.99",
            "words": [
                _word("27-PRODUCE", 0.016722408026755852, 0.49266666666666664, 0.20535117056856186, 0.5143333333333333),
                _word("CANTALOUPE", 0.3183946488294314, 0.51, 0.4956521739130435, 0.5286666666666666),
                _word("MRJ", 0.6762541806020067, 0.5096666666666667, 0.7377926421404682, 0.529),
                _word("1.99", 0.8173913043478261, 0.507, 0.8963210702341137, 0.5283333333333333),
            ],
        },
        {
            "text": "4050 1.99",
            "words": [
                _word("4050", 0.0548494983277592, 0.513, 0.13177257525083613, 0.532),
                _word("1.99", 0.7839464882943143, 0.5253333333333333, 0.8622073578595317, 0.5466666666666666),
            ],
        },
        {
            "text": "81363501124 BLACKBERRIES 60Z MRJ bruten",
            "words": [
                _word("81363501124", 0.05351170568561873, 0.5306666666666666, 0.25418060200668896, 0.5513333333333333),
                _word("BLACKBERRIES 60Z", 0.31906354515050167, 0.5276666666666666, 0.6020066889632107, 0.5486666666666666),
                _word("MRJ", 0.6408026755852843, 0.5276666666666666, 0.7036789297658863, 0.5473333333333333),
                _word("bruten", 0.7605351170568562, 0.5416666666666666, 0.8775919732441472, 0.5573333333333333),
            ],
        },
        {
            "text": "32-SEAFOOD",
            "words": [_word("32-SEAFOOD", 0.015384615384615385, 0.5493333333333333, 0.20602006688963212, 0.5713333333333334)],
        },
        {
            "text": "2146010 SEAFOOD CNTR gnigoQq bn14.99",
            "words": [
                _word("2146010", 0.05618729096989967, 0.5676666666666667, 0.18996655518394648, 0.589),
                _word("SEAFOOD CNTR", 0.3204013377926421, 0.565, 0.5391304347826087, 0.5853333333333334),
                _word("gnigoQq bn14.99", 0.5665551839464883, 0.5643333333333334, 0.8989966555183947, 0.586),
            ],
        },
        {
            "text": "2146010b SEAFOOD CNTR noitqQ 14.99",
            "words": [
                _word("2146010b", 0.06020066889632107, 0.5856666666666667, 0.23277591973244147, 0.6063333333333333),
                _word("SEAFOOD CNTR", 0.3204013377926421, 0.5836666666666667, 0.5337792642140469, 0.606),
                _word("noitqQ", 0.5806020066889632, 0.5863333333333334, 0.705685618729097, 0.6113333333333333),
                _word("14.99", 0.802675585284281, 0.584, 0.8976588628762542, 0.606),
            ],
        },
        {
            "text": "TOTAL 33.97",
            "words": [
                _word("TOTAL", 0.09, 0.65, 0.18, 0.662),
                _word("33.97", 0.88, 0.65, 0.95, 0.662),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = [(item.description, item.price) for item in items]
    assert ("CANTALOUPE", Decimal("1.99")) in pairs
    assert ("BLACKBERRIES 60Z", Decimal("1.99")) in pairs
    assert pairs.count(("SEAFOOD CNTR", Decimal("14.99"))) == 2
