"""Tests for machine-readable `bb api` commands."""

from __future__ import annotations

import importlib
import io
import json
from pathlib import Path

import beanbeaver.runtime.paths as runtime_paths
import beanbeaver.runtime.receipt_storage as receipt_storage
from _pytest.capture import CaptureFixture
from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.cli import main as unified_cli
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
    runtime_paths._paths = None
    importlib.reload(receipt_storage)
    receipt_storage._paths = paths
    return paths


def test_api_list_scanned_returns_json(tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    stage_path = paths.receipts_json_scanned / "2026-03-01_store_12_34_abcd" / "parsed.receipt.json"
    save_stage_document(
        stage_path,
        _stage_document(
            merchant="Store",
            receipt_date="2026-03-01",
            total="12.34",
            stage="parsed",
            stage_index=0,
        ),
    )

    exit_code = unified_cli.main(["api", "list-scanned"])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured == {
        "receipts": [
            {
                "date": "2026-03-01",
                "merchant": "Store",
                "path": str(stage_path),
                "receipt_dir": "2026-03-01_store_12_34_abcd",
                "stage_file": "parsed.receipt.json",
                "total": "12.34",
            }
        ]
    }


def test_api_show_receipt_returns_document(tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    stage_path = paths.receipts_json_approved / "2026-03-02_shop_30_00_beef" / "review_stage_1.receipt.json"
    document = _stage_document(
        merchant="Shop",
        receipt_date="2026-03-02",
        total="30.00",
        stage="review_stage_1",
        stage_index=1,
    )
    save_stage_document(stage_path, document)

    exit_code = unified_cli.main(["api", "show-receipt", str(stage_path)])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["path"] == str(stage_path)
    assert captured["summary"] == {
        "date": "2026-03-02",
        "merchant": "Shop",
        "path": str(stage_path),
        "receipt_dir": "2026-03-02_shop_30_00_beef",
        "stage_file": "review_stage_1.receipt.json",
        "total": "30.00",
    }
    assert captured["document"] == document


def test_api_approve_scanned_moves_receipt_and_creates_review_stage(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    scanned_dir = paths.receipts_json_scanned / "2026-03-03_market_8_50_cafe"
    stage_path = scanned_dir / "parsed.receipt.json"
    save_stage_document(
        stage_path,
        _stage_document(
            merchant="Market",
            receipt_date="2026-03-03",
            total="8.50",
            stage="parsed",
            stage_index=0,
        ),
    )

    exit_code = unified_cli.main(["api", "approve-scanned", str(stage_path)])

    captured = json.loads(capsys.readouterr().out)
    approved_path = Path(captured["approved_path"])
    approved_document = json.loads(approved_path.read_text())
    assert exit_code == 0
    assert captured["status"] == "approved"
    assert captured["source_path"] == str(stage_path)
    assert approved_path.exists()
    assert not stage_path.exists()
    assert approved_path.parent.parent == paths.receipts_json_approved
    assert approved_document["meta"]["stage_index"] == 1
    assert approved_document["meta"]["created_by"] == "tui_review"
    assert approved_document["meta"]["pass_name"] == "tui_approve"


def test_api_approve_scanned_with_review_applies_receipt_overrides(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    scanned_dir = paths.receipts_json_scanned / "2026-03-04_market_10_00_feed"
    stage_path = scanned_dir / "parsed.receipt.json"
    save_stage_document(
        stage_path,
        _stage_document(
            merchant="Market",
            receipt_date="2026-03-04",
            total="10.00",
            stage="parsed",
            stage_index=0,
        ),
    )
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"review": {"merchant": "Better Market", "date": "2026-03-05", "total": "11.25"}})),
    )

    exit_code = unified_cli.main(["api", "approve-scanned-with-review", str(stage_path)])

    captured = json.loads(capsys.readouterr().out)
    approved_path = Path(captured["approved_path"])
    approved_document = json.loads(approved_path.read_text())
    assert exit_code == 0
    assert approved_document["review"] == {
        "merchant": "Better Market",
        "date": "2026-03-05",
        "total": "11.25",
    }
