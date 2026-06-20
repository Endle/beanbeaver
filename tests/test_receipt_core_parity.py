"""Phase-1 parity gate: the consolidated Rust core (`receipt_process_receipt`)
must reproduce the legacy Python chain
(transform_paddleocr_result -> parse_receipt -> format_parsed_receipt) exactly,
across the cached raw-OCR fixtures, using the same bundled default rules.

This proves the OCR-transform + rule-loading glue moved into `receipt-core` is
behavior-preserving before the iOS work depends on it.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from beanbeaver.receipt._rust import require_rust_matcher
from beanbeaver.receipt.formatter import format_parsed_receipt
from beanbeaver.receipt.item_categories import ItemCategoryRuleLayers, build_item_category_rule_layers
from beanbeaver.receipt.ocr_helpers import transform_paddleocr_result
from beanbeaver.receipt.ocr_result_parser import parse_receipt
from beanbeaver.runtime.item_category_rules import _load_toml
from beanbeaver.runtime.merchant_rules import _load_keywords_from_path

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = REPO_ROOT / "rules"
FIXTURE_DIR = REPO_ROOT / "tests" / "receipts_e2e"
CREDIT_CARD_ACCOUNT = "Liabilities:CreditCard:PENDING"

FIXTURES = sorted(FIXTURE_DIR.glob("*.ocr.json"))


def _default_rule_layers() -> ItemCategoryRuleLayers:
    """Default-only item-category layers — the same data the core bundles."""
    classifier = _load_toml(RULES_DIR / "default_item_classifier.toml")
    return build_item_category_rule_layers(classifier_configs=(classifier,), account_configs=())


def _default_known_merchants() -> list[str]:
    return list(_load_keywords_from_path(RULES_DIR / "default_merchant_rules.toml"))


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_core_process_matches_legacy_pipeline(fixture: Path) -> None:
    raw = json.loads(fixture.read_text())
    today = date.today()
    rule_layers = _default_rule_layers()
    known = _default_known_merchants()
    name = fixture.stem

    # --- legacy chain ---
    ocr_doc = transform_paddleocr_result(raw)
    receipt = parse_receipt(
        ocr_doc,
        rule_layers,
        image_filename=name,
        known_merchants=known,
        reference_date=today,
    )
    legacy_beancount = format_parsed_receipt(receipt, credit_card_account=CREDIT_CARD_ACCOUNT, image_sha256=None)

    # --- consolidated Rust core ---
    result = require_rust_matcher().receipt_process_receipt(
        raw,
        image_filename=name,
        known_merchants=known,
        today=(today.year, today.month, today.day),
        credit_card_account=CREDIT_CARD_ACCOUNT,
        image_sha256=None,
    )

    # --- structured parity ---
    assert result["merchant"] == receipt.merchant
    assert result["date_is_placeholder"] == receipt.date_is_placeholder
    if not receipt.date_is_placeholder:
        assert result["date"] == (receipt.date.year, receipt.date.month, receipt.date.day)
    assert result["total"] == str(receipt.total)
    assert (result["tax"] is None) == (receipt.tax is None)
    if receipt.tax is not None:
        assert result["tax"] == str(receipt.tax)
    assert (result["subtotal"] is None) == (receipt.subtotal is None)
    if receipt.subtotal is not None:
        assert result["subtotal"] == str(receipt.subtotal)

    core_items = [tuple(item) for item in result["items"]]
    legacy_items = [(item.description, str(item.price), item.quantity, item.category) for item in receipt.items]
    assert core_items == legacy_items

    core_warnings = [tuple(warning) for warning in result["warnings"]]
    legacy_warnings = [(w.message, w.after_item_index) for w in receipt.warnings]
    assert core_warnings == legacy_warnings

    core_tenders = [tuple(tender) for tender in result["tenders"]]
    legacy_tenders = [(str(t.amount), t.account, t.kind, t.raw_label) for t in receipt.tenders]
    assert core_tenders == legacy_tenders

    # --- headline: rendered beancount text must be byte-identical ---
    assert result["beancount"] == legacy_beancount


def test_fixtures_present() -> None:
    """Guard against silently testing nothing if fixtures move."""
    assert FIXTURES, "no *.ocr.json fixtures found under tests/receipts_e2e"
