from __future__ import annotations

import importlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import beanbeaver.runtime.paths as runtime_paths
from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.runtime.item_category_rules import (
    load_item_category_rule_layers,
    load_receipt_structuring_rule_layers,
)


def _reload_receipt_storage(tmp_path: Path, monkeypatch: MonkeyPatch):
    monkeypatch.setenv("BEANBEAVER_ROOT", str(tmp_path))
    runtime_paths._paths = runtime_paths.ProjectPaths(root=tmp_path)
    load_item_category_rule_layers.cache_clear()
    load_receipt_structuring_rule_layers.cache_clear()

    import beanbeaver.runtime.receipt_storage as receipt_storage

    return importlib.reload(receipt_storage)


def test_list_approved_receipts_migrates_legacy_flat_files(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    legacy_dir = tmp_path / "receipts" / "approved"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "2026-02-15_costco_124_60.beancount"
    original_content = """; === PARSED RECEIPT - AWAITING CC MATCH ===
; @merchant: COSTCO
; @date: 2026-02-15
; @total: 124.60
; @items: 3
; @image: receipt_20260219_211153.jpg
; @image_filename: receipt_20260219_211153.jpg
; @image_sha256: aabcdf8f543246ecaa01183f4571a9dd8152f039a30fa89bbdb80ba3a03b9599

2026-02-15 * "COSTCO" "Receipt scan"
  Liabilities:CreditCard:PENDING        -124.60 CAD
  Expenses:Food:Grocery:Dairy              6.69 CAD  ; 435259 2% FINE-FILT
  Expenses:PersonalCare:Tooth             59.99 CAD  ; 2670056 SONICARE
  Expenses:PersonalCare:Tooth            -12.00 CAD  ; 2026263 TPD/2670056
  Expenses:Tax:HST                        11.27 CAD

; --- Raw OCR Text (for reference) ---
; TOTAL 124.60
; MasterCard 124.60
"""
    legacy_file.write_text(original_content, encoding="utf-8")

    receipt_storage = _reload_receipt_storage(tmp_path, monkeypatch)

    receipts = receipt_storage.list_approved_receipts()

    assert len(receipts) == 1
    stage_path, merchant, receipt_date, total = receipts[0]
    assert merchant == "COSTCO"
    assert receipt_date == date(2026, 2, 15)
    assert total == Decimal("124.60")

    receipt_dir = stage_path.parent.parent
    assert receipt_dir.parent == tmp_path / "receipts"
    assert stage_path.parent == receipt_dir / "stages"
    assert stage_path.name == "010_review.receipt.json"
    assert not legacy_file.exists()

    rendered_path = receipt_dir / "rendered" / "current.beancount"
    assert rendered_path.read_text(encoding="utf-8") == original_content

    migrated = receipt_storage.parse_receipt_from_stage_json(stage_path)
    assert migrated.merchant == "COSTCO"
    assert migrated.date == date(2026, 2, 15)
    assert migrated.total == Decimal("124.60")
    assert migrated.image_filename == "receipt_20260219_211153.jpg"
    assert migrated.raw_text == "TOTAL 124.60\nMasterCard 124.60"
    assert [item.category for item in migrated.items] == [
        "Expenses:Food:Grocery:Dairy",
        "Expenses:PersonalCare:Tooth",
        "Expenses:PersonalCare:Tooth",
    ]
    assert [item.price for item in migrated.items] == [
        Decimal("6.69"),
        Decimal("59.99"),
        Decimal("-12.00"),
    ]

    document = receipt_storage.load_stage_document(stage_path)
    assert document["meta"]["image_sha256"] == "aabcdf8f543246ecaa01183f4571a9dd8152f039a30fa89bbdb80ba3a03b9599"
