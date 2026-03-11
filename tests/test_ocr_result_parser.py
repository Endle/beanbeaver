from __future__ import annotations

import pytest
from _pytest.monkeypatch import MonkeyPatch

from beanbeaver.receipt import ocr_result_parser
from beanbeaver.receipt.receipt_structuring import parse_receipt
from beanbeaver.runtime.item_category_rules import load_receipt_structuring_rule_layers


def test_parse_receipt_parses_simple_text_receipt() -> None:
    receipt = parse_receipt(
        {
            "full_text": "TEST SHOP\nMILK 2.50\nTOTAL 2.50",
            "pages": [],
        },
        item_category_rule_layers=load_receipt_structuring_rule_layers(),
    )

    assert receipt.merchant == "TEST SHOP"
    assert str(receipt.total) == "2.50"
    assert len(receipt.items) == 1
    assert receipt.items[0].description == "MILK"
    assert str(receipt.items[0].price) == "2.50"


def test_parse_receipt_raises_when_spatial_backend_is_unavailable(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(ocr_result_parser, "_extract_items_with_bbox", _raise_missing_spatial_backend)
    monkeypatch.setattr(ocr_result_parser, "_extract_items", _unexpected_text_fallback)

    ocr_result = {
        "full_text": "T&T SUPERMARKET\nPRODUCE W $2.68\nTOTAL 2.68",
        "pages": [
            {
                "lines": [
                    {
                        "text": "PRODUCE W $2.68",
                        "words": [
                            {
                                "text": "PRODUCE",
                                "bbox": {"left": 0.02, "top": 0.15, "right": 0.12, "bottom": 0.17},
                            }
                        ],
                    }
                ]
            }
        ],
    }

    with pytest.raises(ImportError, match="beanbeaver\\._rust_matcher is required"):
        parse_receipt(
            ocr_result,
            item_category_rule_layers=load_receipt_structuring_rule_layers(),
        )


def _raise_missing_spatial_backend(*args: object, **kwargs: object) -> object:
    raise ImportError("beanbeaver._rust_matcher is required for spatial receipt parsing")


def _unexpected_text_fallback(*args: object, **kwargs: object) -> object:
    raise AssertionError("text fallback should not run for spatial receipts without the native backend")
