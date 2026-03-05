from decimal import Decimal

from beanbeaver.receipt.ocr_parser.common import _is_spatial_layout_receipt
from beanbeaver.receipt.ocr_parser.items_spatial_parser import _extract_items_with_bbox
from beanbeaver.runtime.item_category_rules import load_item_category_rule_layers


def _word(text: str, x_left: float, y_top: float, x_right: float, y_bottom: float) -> dict:
    return {
        "text": text,
        "bbox": [[x_left, y_top], [x_right, y_bottom]],
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
        item_category_rule_layers=load_item_category_rule_layers(),
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
        item_category_rule_layers=load_item_category_rule_layers(),
    )

    assert any(item.description == "LUNCH MEAT" and item.price == Decimal("3.50") for item in items)
