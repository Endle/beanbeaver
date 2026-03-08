from datetime import date
from decimal import Decimal

import pytest
from beanbeaver.domain.receipt import Receipt, ReceiptItem, ReceiptWarning
from beanbeaver.receipt.beancount_rendering import render_stage_document_as_beancount
from beanbeaver.receipt.receipt_structuring import (
    build_parsed_receipt_stage,
    receipt_from_stage_document,
)
from beanbeaver.runtime.item_category_rules import load_item_category_rule_layers


def test_receipt_stage_resolves_review_overrides_and_removed_items() -> None:
    rule_layers = load_item_category_rule_layers()
    receipt = Receipt(
        merchant="NOFRILLS",
        date=date(2026, 3, 3),
        total=Decimal("46.56"),
        subtotal=Decimal("41.20"),
        tax=Decimal("5.36"),
        raw_text="NOFRILLS\nTOTAL 46.56",
        image_filename="nofrills.jpg",
        items=[
            ReceiptItem(
                description="Napa",
                price=Decimal("3.17"),
                quantity=1,
                category="Expenses:Food:Grocery:Vegetable",
            ),
            ReceiptItem(
                description="Milk",
                price=Decimal("4.99"),
                quantity=1,
                category="Expenses:Food:Grocery:Dairy",
            ),
        ],
        warnings=[ReceiptWarning(message="parser warning", after_item_index=0)],
    )

    document = build_parsed_receipt_stage(receipt, rule_layers=rule_layers)
    document["review"] = {"merchant": "NO FRILLS", "total": "46.56"}
    document["items"][0]["review"] = {
        "description": "Napa cabbage",
        "classification": {"tags": ["grocery", "vegetable", "fresh", "cabbage"]},
    }
    document["items"][1]["review"] = {"removed": True}

    resolved = receipt_from_stage_document(document, rule_layers=rule_layers)

    assert resolved.merchant == "NO FRILLS"
    assert [item.description for item in resolved.items] == ["Napa cabbage"]
    assert resolved.items[0].category == "Expenses:Food:Grocery:Vegetable"
    assert resolved.warnings[0].message == "parser warning"


def test_render_stage_document_requires_total() -> None:
    rule_layers = load_item_category_rule_layers()
    receipt = Receipt(
        merchant="TEST",
        date=date(2026, 3, 3),
        total=Decimal("1.00"),
        items=[],
    )
    document = build_parsed_receipt_stage(receipt, rule_layers=rule_layers)
    document["receipt"]["total"] = None

    with pytest.raises(ValueError, match="receipt total is missing"):
        render_stage_document_as_beancount(document, rule_layers=rule_layers)
