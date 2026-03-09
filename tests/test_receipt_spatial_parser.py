from decimal import Decimal

import pytest
from beanbeaver.receipt.ocr_parser.items_spatial_parser import _rust_matcher, _select_spatial_item_line
from beanbeaver.receipt.receipt_structuring.parsers.common import _is_spatial_layout_receipt
from beanbeaver.receipt.receipt_structuring.parsers.items_spatial_parser import _extract_items_with_bbox
from beanbeaver.runtime.item_category_rules import load_receipt_structuring_rule_layers

pytestmark = pytest.mark.skipif(_rust_matcher is None, reason="beanbeaver._rust_matcher is not built")


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


def test_extract_items_with_bbox_keeps_short_produce_name_alignment() -> None:
    # Reproduces C&C-style produce rows where short item names (e.g., "Napa")
    # are followed by weight lines with trailing totals.
    lines = [
        {
            "text": "&& 02-Vegetable",
            "words": [_word("&& 02-Vegetable", 0.15, 0.355, 0.30, 0.364)],
        },
        {
            "text": "Napa",
            "words": [_word("Napa", 0.06, 0.365, 0.09, 0.372)],
        },
        {
            "text": "2.46 1b @ $1.29/1b 3.17",
            "words": [
                _word("2.46 1b @ $1.29/1b", 0.20, 0.378, 0.27, 0.386),
                _word("3.17", 0.89, 0.377, 0.92, 0.384),
            ],
        },
        {
            "text": "Soybean Sprout",
            "words": [_word("Soybean Sprout", 0.12, 0.388, 0.24, 0.395)],
        },
        {
            "text": "0.65 1b @ $1.58/1b 1.03",
            "words": [
                _word("0.65 1b @ $1.58/1b", 0.21, 0.401, 0.28, 0.409),
                _word("1.03", 0.89, 0.400, 0.92, 0.407),
            ],
        },
        {
            "text": "Fresh Baby Shanghai Miu",
            "words": [_word("Fresh Baby Shanghai Miu", 0.12, 0.410, 0.34, 0.417)],
        },
        {
            "text": "1.30 1b @ $2.59/1b 3.37",
            "words": [
                _word("1.30 1b @ $2.59/1b", 0.21, 0.423, 0.28, 0.431),
                _word("3.37", 0.89, 0.422, 0.92, 0.429),
            ],
        },
        {
            "text": "Coriander x2",
            "words": [
                _word("Coriander", 0.10, 0.439, 0.17, 0.446),
                _word("x2", 0.70, 0.438, 0.73, 0.445),
            ],
        },
        {
            "text": "(2 /for $3.00) 2 /for 3.00",
            "words": [
                _word("(2 /for $3.00)", 0.16, 0.452, 0.27, 0.460),
                _word("2 /for", 0.55, 0.451, 0.61, 0.459),
                _word("3.00", 0.89, 0.450, 0.92, 0.457),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = [(item.description, item.price) for item in items]
    assert ("Napa", Decimal("3.17")) in pairs
    assert ("Soybean Sprout", Decimal("1.03")) in pairs
    assert ("Fresh Baby Shanghai Miu", Decimal("3.37")) in pairs
    assert ("Coriander", Decimal("3.00")) in pairs


def test_is_spatial_layout_receipt_detects_nofrills() -> None:
    assert _is_spatial_layout_receipt([], "NOFRILLS\nPETER & SUZI'S NF MARKHAM\nTOTAL 46.56")


def test_extract_items_with_bbox_accepts_spaced_decimal_price_words() -> None:
    lines = [
        {
            "text": "(2)05707200195 LUNCH MEAT MRJ",
            "words": [
                _word("(2)05707200195 LUNCH MEAT", 0.06, 0.167, 0.59, 0.189),
                _word("MRJ", 0.664, 0.172, 0.741, 0.191),
            ],
        },
        {
            "text": "2 @ $1.75 3. 50",
            "words": [
                _word("2 @ $1.75", 0.099, 0.188, 0.297, 0.207),
                _word("3. 50", 0.856, 0.190, 0.955, 0.210),
            ],
        },
        {
            "text": "TOTAL 3.50",
            "words": [
                _word("TOTAL", 0.08, 0.520, 0.18, 0.540),
                _word("3.50", 0.86, 0.520, 0.94, 0.540),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert any(item.description == "LUNCH MEAT" and item.price == Decimal("3.50") for item in items)


def test_extract_items_with_bbox_keeps_next_priced_row_from_stealing_quantity_total() -> None:
    lines = [
        {
            "text": "(2)05707200195 LUNCH MEAT MRJ",
            "words": [
                _word("(2)05707200195 LUNCH MEAT", 0.06, 0.167, 0.59, 0.189),
                _word("MRJ", 0.664, 0.172, 0.741, 0.191),
            ],
        },
        {
            "text": "2 @ $1.75 3. 50",
            "words": [
                _word("2 @ $1.75", 0.099, 0.188, 0.297, 0.207),
                _word("3. 50", 0.856, 0.190, 0.955, 0.210),
            ],
        },
        {
            "text": "06700011056 SPRITE ZERO HMRJ 8.69",
            "words": [
                _word("06700011056 SPRITE ZERO", 0.099, 0.207, 0.508, 0.225),
                _word("HMRJ", 0.663, 0.212, 0.760, 0.225),
                _word("8.69", 0.858, 0.209, 0.950, 0.229),
            ],
        },
        {
            "text": "06780000102 VEG OIL MRJ 6. 99",
            "words": [
                _word("06780000102 VEG OIL", 0.099, 0.226, 0.459, 0.243),
                _word("MRJ", 0.663, 0.229, 0.741, 0.246),
                _word("6. 99", 0.856, 0.228, 0.951, 0.247),
            ],
        },
        {
            "text": "TOTAL 19.18",
            "words": [
                _word("TOTAL", 0.08, 0.520, 0.18, 0.540),
                _word("19.18", 0.86, 0.520, 0.94, 0.540),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = [(item.description, item.price) for item in items]
    assert ("LUNCH MEAT", Decimal("3.50")) in pairs
    assert ("SPRITE ZERO", Decimal("8.69")) in pairs
    assert ("VEG OIL", Decimal("6.99")) in pairs


def test_extract_items_with_bbox_prefers_item_above_for_count_price_rows() -> None:
    lines = [
        {
            "text": "#Hsu Fu Chi Crispy Shrimp",
            "words": [_word("#Hsu Fu Chi Crispy Shrimp", 0.12, 0.215, 0.42, 0.226)],
        },
        {
            "text": "2 @ $1.99 3.98",
            "words": [
                _word("2 @ $1.99", 0.08, 0.229, 0.17, 0.239),
                _word("3.98", 0.89, 0.229, 0.95, 0.239),
            ],
        },
        {
            "text": "Potato Puffed Food Seawee",
            "words": [_word("Potato Puffed Food Seawee", 0.20, 0.237, 0.46, 0.247)],
        },
        {
            "text": "2 @ $1.99 3.98",
            "words": [
                _word("2 @ $1.99", 0.08, 0.252, 0.17, 0.263),
                _word("3.98", 0.89, 0.252, 0.95, 0.263),
            ],
        },
        {
            "text": "Chen Ke Ming Original Th",
            "words": [_word("Chen Ke Ming Original Th", 0.20, 0.268, 0.46, 0.279)],
        },
        {
            "text": "(2 /for $7.00) 2 /for 7.00",
            "words": [
                _word("(2 /for $7.00)", 0.16, 0.283, 0.31, 0.294),
                _word("2 /for", 0.54, 0.283, 0.62, 0.294),
                _word("7.00", 0.89, 0.283, 0.95, 0.294),
            ],
        },
        {
            "text": "TOTAL 14.96",
            "words": [
                _word("TOTAL", 0.08, 0.520, 0.18, 0.540),
                _word("14.96", 0.86, 0.520, 0.94, 0.540),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = [(item.description, item.price) for item in items]
    assert ("Hsu Fu Chi Crispy Shrimp", Decimal("3.98")) in pairs
    assert ("Potato Puffed Food Seawee", Decimal("3.98")) in pairs
    assert ("Chen Ke Ming Original Th", Decimal("7.00")) in pairs


def test_extract_items_with_bbox_prefers_item_above_onsale_price() -> None:
    # Reproduces C&C rows where an ON SALE line carries the price for the
    # preceding item, while the quantity line below belongs to the next item.
    lines = [
        {
            "text": "*S & B Wasabi",
            "words": [_word("*S & B Wasabi", 0.08, 0.100, 0.260, 0.112)],
        },
        {
            "text": "(E)ON SALE 1.98",
            "words": [
                _word("(E)ON SALE", 0.09, 0.120, 0.210, 0.132),
                _word("1.98", 0.88, 0.120, 0.93, 0.132),
            ],
        },
        {
            "text": "2 @ $0.99 4.59",
            "words": [
                _word("2 @ $0.99", 0.22, 0.140, 0.320, 0.152),
                _word("4.59", 0.88, 0.140, 0.93, 0.152),
            ],
        },
        {
            "text": "Hot Kid Honey Flavour Bal",
            "words": [_word("Hot Kid Honey Flavour Bal", 0.08, 0.160, 0.360, 0.172)],
        },
        {
            "text": "TOTAL 6.57",
            "words": [
                _word("TOTAL", 0.09, 0.500, 0.180, 0.512),
                _word("6.57", 0.88, 0.500, 0.93, 0.512),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = [(item.description, item.price) for item in items]
    assert pairs == [
        ("S & B Wasabi", Decimal("1.98")),
        ("Hot Kid Honey Flavour Bal", Decimal("4.59")),
    ]


def test_extract_items_with_bbox_keeps_following_priced_row_from_stealing_multibuy_total() -> None:
    # Reproduces the private C&C fixture where the next priced row sits within
    # the same Y tolerance band as the 4.59 multi-buy total.
    lines = [
        {
            "text": "*S & B Wasabi",
            "words": [_word("*S & B Wasabi", 0.00, 0.159, 0.309, 0.172)],
        },
        {
            "text": "(E)ON SALE 1.98",
            "words": [
                _word("(E)ON SALE", 0.129, 0.171, 0.307, 0.180),
                _word("1.98", 0.897, 0.180, 0.951, 0.188),
            ],
        },
        {
            "text": "2 @ $0.99 4.59",
            "words": [
                _word("2 @ $0.99", 0.081, 0.180, 0.167, 0.191),
                _word("4.59", 0.889, 0.189, 0.950, 0.197),
            ],
        },
        {
            "text": "Hot Kid Honey Flavour Bal",
            "words": [_word("Hot Kid Honey Flavour Bal", 0.192, 0.185, 0.429, 0.200)],
        },
        {
            "text": "*Udon Noodles With Tonkots 3.99",
            "words": [
                _word("*Udon Noodles With Tonkots", 0.112, 0.204, 0.496, 0.219),
                _word("3.99", 0.884, 0.207, 0.949, 0.216),
            ],
        },
        {
            "text": "ONSAL 3.99",
            "words": [
                _word("ONSAL", 0.312, 0.217, 0.379, 0.223),
                _word("3.99", 0.882, 0.224, 0.948, 0.234),
            ],
        },
        {
            "text": "*Lucky Pearl Shanghai Dry",
            "words": [_word("*Lucky Pearl Shanghai Dry", 0.108, 0.222, 0.472, 0.236)],
        },
        {
            "text": "TOTAL 10.56",
            "words": [
                _word("TOTAL", 0.09, 0.500, 0.180, 0.512),
                _word("10.56", 0.88, 0.500, 0.94, 0.512),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = [(item.description, item.price) for item in items]
    assert ("Hot Kid Honey Flavour Bal", Decimal("4.59")) in pairs
    assert ("Udon Noodles With Tonkots", Decimal("3.99")) in pairs
    assert ("Lucky Pearl Shanghai Dry", Decimal("3.99")) in pairs
    assert not any(
        item.description == "Udon Noodles With Tonkots" and item.price == Decimal("4.59") for item in items
    )
    assert not any(
        item.description == "Hot Kid Honey Flavour Bal" and item.price == Decimal("3.99") for item in items
    )


def test_extract_items_with_bbox_accepts_following_item_for_tnt_department_price_rows() -> None:
    lines = [
        {
            "text": "PRODUCE W $2.68",
            "words": [
                _word("PRODUCE", 0.02, 0.150, 0.12, 0.170),
                _word("W $2.68", 0.84, 0.164, 0.93, 0.180),
            ],
        },
        {
            "text": "(SALE) WHITE POMELO",
            "words": [_word("(SALE) WHITE POMELO", 0.05, 0.176, 0.30, 0.188)],
        },
        {
            "text": "DELI W $4.99",
            "words": [
                _word("DELI", 0.02, 0.530, 0.06, 0.544),
                _word("W $4.99", 0.87, 0.545, 0.95, 0.563),
            ],
        },
        {
            "text": "NEILSON JOYYA CHOCOLATE E MILK",
            "words": [
                _word("NEILSON JOYYA CHOCOLATE", 0.08, 0.552, 0.36, 0.566),
                _word("E MILK", 0.40, 0.552, 0.53, 0.566),
            ],
        },
        {
            "text": "TOTAL 7.67",
            "words": [
                _word("TOTAL", 0.09, 0.700, 0.180, 0.712),
                _word("7.67", 0.88, 0.700, 0.93, 0.712),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    pairs = [(item.description, item.price) for item in items]
    assert ("WHITE POMELO", Decimal("2.68")) in pairs
    assert ("NEILSON JOYYA CHOCOLATE E MILK", Decimal("4.99")) in pairs


def test_extract_items_with_bbox_keeps_cash_prefix_product_name() -> None:
    lines = [
        {
            "text": "CASHMERE BATHROOM TISSUE 9.99",
            "words": [
                _word("CASHMERE BATHROOM TISSUE", 0.08, 0.100, 0.380, 0.112),
                _word("9.99", 0.88, 0.100, 0.93, 0.112),
            ],
        },
        {
            "text": "TOTAL 9.99",
            "words": [
                _word("TOTAL", 0.09, 0.500, 0.180, 0.512),
                _word("9.99", 0.88, 0.500, 0.93, 0.512),
            ],
        },
    ]

    items = _extract_items_with_bbox(
        pages=[{"lines": lines}],
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert len(items) == 1
    assert items[0].description == "CASHMERE BATHROOM TISSUE"
    assert items[0].price == Decimal("9.99")


def test_select_spatial_item_line_uses_rust_backend_when_required() -> None:
    assert _rust_matcher is not None

    result = _select_spatial_item_line(
        0.20,
        [
            {
                "line_y": 0.18,
                "is_used": False,
                "is_valid_item_line": True,
                "has_trailing_price": False,
                "looks_like_quantity_expression": False,
            },
            {
                "line_y": 0.20,
                "is_used": False,
                "is_valid_item_line": True,
                "has_trailing_price": True,
                "looks_like_quantity_expression": False,
            },
        ],
        prefer_below=False,
        price_line_has_onsale=False,
    )

    assert result == (1, 0.0)
