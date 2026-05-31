from datetime import date
from decimal import Decimal

import pytest
from beanbeaver.domain.receipt import Receipt, ReceiptItem, ReceiptWarning, Tender
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
        "price": "3.99",
        "notes": "Picked the fresher head",
        "classification": {"tags": ["grocery", "vegetable", "fresh", "cabbage"]},
    }
    document["items"][1]["review"] = {"removed": True}

    resolved = receipt_from_stage_document(document, rule_layers=rule_layers)

    assert resolved.merchant == "NO FRILLS"
    assert [item.description for item in resolved.items] == ["Napa cabbage"]
    assert resolved.items[0].price == Decimal("3.99")
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


def test_tenders_round_trip_through_staged_json() -> None:
    rule_layers = load_item_category_rule_layers()
    receipt = Receipt(
        merchant="COSTCO",
        date=date(2026, 3, 7),
        total=Decimal("466.68"),
        items=[],
        tax=Decimal("5.72"),
        raw_text="",
        image_filename="costco.jpg",
        tenders=[
            Tender(amount=Decimal("441.68"), kind="card", raw_label="MasterCard"),
            Tender(amount=Decimal("25.00"), kind="gift_card", raw_label="Shop Card"),
        ],
    )
    document = build_parsed_receipt_stage(receipt, rule_layers=rule_layers)
    assert document["meta"]["schema_version"] == "2"
    assert document["tenders"] == [
        {"amount": "441.68", "account": None, "kind": "card", "raw_label": "MasterCard"},
        {"amount": "25.00", "account": None, "kind": "gift_card", "raw_label": "Shop Card"},
    ]

    resolved = receipt_from_stage_document(document, rule_layers=rule_layers)
    assert [t.amount for t in resolved.tenders] == [Decimal("441.68"), Decimal("25.00")]
    assert [t.kind for t in resolved.tenders] == ["card", "gift_card"]


def test_schema_v1_document_loads_as_empty_tenders() -> None:
    rule_layers = load_item_category_rule_layers()
    receipt = Receipt(
        merchant="TEST",
        date=date(2026, 3, 7),
        total=Decimal("10.00"),
        items=[],
        image_filename="legacy.jpg",
    )
    document = build_parsed_receipt_stage(receipt, rule_layers=rule_layers)
    # Simulate an old schema-1 document with no tenders key.
    del document["tenders"]
    document["meta"]["schema_version"] = "1"

    resolved = receipt_from_stage_document(document, rule_layers=rule_layers)
    assert resolved.tenders == []


def test_tender_review_patch_overrides_account() -> None:
    rule_layers = load_item_category_rule_layers()
    receipt = Receipt(
        merchant="COSTCO",
        date=date(2026, 3, 7),
        total=Decimal("466.68"),
        items=[],
        image_filename="costco.jpg",
        tenders=[
            Tender(amount=Decimal("25.00"), kind="gift_card", raw_label="Shop Card"),
            Tender(amount=Decimal("441.68"), kind="card", raw_label="MasterCard"),
        ],
    )
    document = build_parsed_receipt_stage(receipt, rule_layers=rule_layers)
    # User assigns a real gift-card asset account via the review patch.
    document["tenders"][0]["review"] = {"account": "Assets:GiftCards:Costco"}

    resolved = receipt_from_stage_document(document, rule_layers=rule_layers)
    accounts = [t.account for t in resolved.tenders]
    assert accounts == ["Assets:GiftCards:Costco", None]
