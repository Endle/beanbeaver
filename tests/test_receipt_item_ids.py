"""Tests for preserving receipt item IDs across parse/format/storage flows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from beanbeaver.domain.receipt import Receipt, ReceiptItem
from beanbeaver.receipt.formatter import format_draft_beancount, format_parsed_receipt
from beanbeaver.receipt.ocr_parser.common import _extract_leading_item_id
from beanbeaver.receipt.ocr_parser.items_text_parser import _extract_items
from beanbeaver.runtime.item_category_rules import load_item_category_rule_layers
from beanbeaver.runtime.receipt_storage import parse_receipt_from_beancount


def test_extract_leading_item_id_helper() -> None:
    assert _extract_leading_item_id("(2) 62843020000 DOUGHNUTS MRJ") == ("62843020000", "DOUGHNUTS MRJ")
    assert _extract_leading_item_id("BANANA") == (None, "BANANA")


def test_text_parser_preserves_leading_item_id() -> None:
    lines = ["62843020000 DOUGHNUTS MRJ 12.00 H"]
    items = _extract_items(
        lines,
        summary_amounts=set(),
        item_category_rule_layers=load_item_category_rule_layers(),
    )
    assert len(items) == 1
    assert items[0].item_id == "62843020000"
    assert items[0].description == "DOUGHNUTS MRJ"
    assert items[0].price == Decimal("12.00")


def test_formatter_emits_item_id_in_comments() -> None:
    receipt = Receipt(
        merchant="Test Merchant",
        date=date(2026, 2, 1),
        total=Decimal("3.00"),
        items=[
            ReceiptItem(
                description="BANANA",
                price=Decimal("3.00"),
                quantity=2,
                category="Expenses:Food:Produce",
                item_id="4011",
            )
        ],
    )
    parsed_output = format_parsed_receipt(receipt)
    draft_output = format_draft_beancount(receipt)
    assert "BANANA (qty 2) [item_id:4011]" in parsed_output
    assert "BANANA (qty 2) [item_id:4011]" in draft_output


def test_receipt_storage_roundtrip_parses_item_id(tmp_path: Path) -> None:
    receipt = Receipt(
        merchant="Test Merchant",
        date=date(2026, 2, 1),
        total=Decimal("3.00"),
        items=[
            ReceiptItem(
                description="BANANA",
                price=Decimal("3.00"),
                quantity=2,
                category="Expenses:Food:Produce",
                item_id="4011",
            )
        ],
    )
    content = format_parsed_receipt(receipt)
    path = tmp_path / "receipt.beancount"
    path.write_text(content)
    loaded = parse_receipt_from_beancount(path)
    assert len(loaded.items) == 1
    assert loaded.items[0].description == "BANANA"
    assert loaded.items[0].quantity == 2
    assert loaded.items[0].item_id == "4011"
