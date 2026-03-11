"""Tests for the thin Python wrappers over the native match service."""

from __future__ import annotations

import importlib
from decimal import Decimal
from pathlib import Path

import beanbeaver.runtime.paths as runtime_paths
import beanbeaver.runtime.receipt_storage as receipt_storage
from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.application import match_service
from beanbeaver.receipt.receipt_structuring import save_stage_document
from beanbeaver.runtime.paths import ProjectPaths


def _stage_document(*, merchant: str, receipt_date: str, total: str, stage: str, stage_index: int) -> dict[str, object]:
    return {
        "meta": {
            "schema_version": "1",
            "receipt_id": f"id-{merchant.lower()}",
            "stage": stage,
            "stage_index": stage_index,
            "created_at": "2026-03-07T00:00:00Z",
            "created_by": "test",
            "pass_name": "test",
        },
        "receipt": {
            "merchant": merchant,
            "date": receipt_date,
            "currency": "CAD",
            "subtotal": total,
            "tax": "0.00",
            "total": total,
        },
        "items": [],
        "warnings": [],
        "raw_text": None,
        "debug": None,
    }


def _configure_temp_root(tmp_path: Path, monkeypatch: MonkeyPatch) -> ProjectPaths:
    paths = ProjectPaths(root=tmp_path)
    monkeypatch.setenv("BEANBEAVER_ROOT", str(tmp_path))
    runtime_paths.reset_paths()
    importlib.reload(receipt_storage)
    return paths


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_plan_receipt_matches_returns_typed_plans(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    _write(
        paths.main_beancount,
        """
option "operating_currency" "CAD"
2026-01-01 open Liabilities:CreditCard:CardA CAD
2026-01-01 open Expenses:Food CAD
include "records/2026/carda_0101_0131.beancount"
""".lstrip(),
    )
    statement_path = paths.records / "2026" / "carda_0101_0131.beancount"
    _write(
        statement_path,
        """
2026-03-04 * "Market" ""
  Liabilities:CreditCard:CardA -10.00 CAD
  Expenses:Food 10.00 CAD
""".lstrip(),
    )

    matchable_path = paths.receipts_json_approved / "2026-03-04_market_10_00_feed" / "review_stage_1.receipt.json"
    unmatched_path = paths.receipts_json_approved / "2026-03-05_other_25_00_feed" / "review_stage_1.receipt.json"
    save_stage_document(
        matchable_path,
        _stage_document(
            merchant="Market",
            receipt_date="2026-03-04",
            total="10.00",
            stage="review_stage_1",
            stage_index=1,
        ),
    )
    save_stage_document(
        unmatched_path,
        _stage_document(
            merchant="Other",
            receipt_date="2026-03-05",
            total="25.00",
            stage="review_stage_1",
            stage_index=1,
        ),
    )

    plans = match_service.plan_receipt_matches([matchable_path, unmatched_path])

    assert [plan.path for plan in plans] == [matchable_path, unmatched_path]
    assert plans[0].errors == []
    assert len(plans[0].candidates) == 1
    assert plans[0].candidates[0].file_path == str(statement_path)
    assert plans[0].candidates[0].date.isoformat() == "2026-03-04"
    assert plans[0].candidates[0].amount == Decimal("10.00")
    assert plans[1].errors == []
    assert plans[1].candidates == []
    assert plans[1].warning == "No reliable matches found, and no weaker fallback candidates were found."
