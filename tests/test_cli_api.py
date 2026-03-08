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
    runtime_paths.reset_paths()
    importlib.reload(receipt_storage)
    return paths


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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


def test_api_list_scanned_uses_configured_project_root(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    package_root = tmp_path / "package"
    project_root = tmp_path / "project"
    (package_root / "config").mkdir(parents=True)
    paths = ProjectPaths(root=project_root)
    monkeypatch.setattr(runtime_paths, "_PACKAGE_ROOT", package_root)
    monkeypatch.delenv("BEANBEAVER_ROOT", raising=False)
    (package_root / "config" / "tui.json").write_text(
        json.dumps({"project_root": "../project"}),
        encoding="utf-8",
    )
    runtime_paths.reset_paths()
    importlib.reload(receipt_storage)

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
    assert captured["receipts"][0]["path"] == str(stage_path)


def test_api_show_receipt_returns_document(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
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


def test_api_re_edit_approved_with_review_applies_receipt_overrides(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    approved_dir = paths.receipts_json_approved / "2026-03-04_market_10_00_feed"
    stage_path = approved_dir / "review_stage_1.receipt.json"
    save_stage_document(
        stage_path,
        _stage_document(
            merchant="Market",
            receipt_date="2026-03-04",
            total="10.00",
            stage="review_stage_1",
            stage_index=1,
        ),
    )
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"review": {"merchant": "Better Market", "date": "2026-03-05", "total": "11.25"}})),
    )

    exit_code = unified_cli.main(["api", "re-edit-approved-with-review", str(stage_path)])

    captured = json.loads(capsys.readouterr().out)
    updated_path = Path(captured["updated_path"])
    updated_document = json.loads(updated_path.read_text())
    assert exit_code == 0
    assert captured["status"] == "updated"
    assert updated_document["review"] == {
        "merchant": "Better Market",
        "date": "2026-03-05",
        "total": "11.25",
    }


def test_api_match_candidates_and_apply_match_for_approved_receipt(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
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
    approved_dir = paths.receipts_json_approved / "2026-03-04_market_10_00_feed"
    stage_path = approved_dir / "review_stage_1.receipt.json"
    save_stage_document(
        stage_path,
        _stage_document(
            merchant="Market",
            receipt_date="2026-03-04",
            total="10.00",
            stage="review_stage_1",
            stage_index=1,
        ),
    )

    exit_code = unified_cli.main(["api", "match-candidates", str(stage_path)])
    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["errors"] == []
    assert len(captured["candidates"]) == 1

    candidate = captured["candidates"][0]
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"file_path": candidate["file_path"], "line_number": candidate["line_number"]})),
    )
    exit_code = unified_cli.main(["api", "apply-match", str(stage_path)])
    applied = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert applied["status"] in {"applied", "already_applied"}
    assert Path(applied["matched_receipt_path"]).exists()
    assert Path(applied["enriched_path"]).exists()
    assert applied["enriched_path"].endswith("2026-03-04_market_10_00_feed.beancount")
    updated_statement = statement_path.read_text(encoding="utf-8")
    assert 'include "_enriched/2026-03-04_market_10_00_feed.beancount"' in updated_statement


def test_api_match_candidates_falls_back_to_weaker_candidates(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
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
  Liabilities:CreditCard:CardA -10.70 CAD
  Expenses:Food 10.70 CAD
""".lstrip(),
    )
    approved_dir = paths.receipts_json_approved / "2026-03-04_market_10_00_feed"
    stage_path = approved_dir / "review_stage_1.receipt.json"
    save_stage_document(
        stage_path,
        _stage_document(
            merchant="Market",
            receipt_date="2026-03-04",
            total="10.00",
            stage="review_stage_1",
            stage_index=1,
        ),
    )

    exit_code = unified_cli.main(["api", "match-candidates", str(stage_path)])
    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["errors"] == []
    assert captured["warning"] == "No reliable matches found. Showing weaker candidates for manual review."
    assert len(captured["candidates"]) == 1

    candidate = captured["candidates"][0]
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"file_path": candidate["file_path"], "line_number": candidate["line_number"]})),
    )
    exit_code = unified_cli.main(["api", "apply-match", str(stage_path)])
    applied = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert applied["status"] in {"applied", "already_applied"}
    assert applied["message"].startswith("Weak candidate applied after relaxed fallback.")


def test_api_get_config_returns_resolved_project_root(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    package_root = tmp_path / "package"
    project_root = tmp_path / "project"
    (package_root / "config").mkdir(parents=True)
    project_root.mkdir()
    (project_root / "main.beancount").write_text("", encoding="utf-8")
    monkeypatch.setattr(runtime_paths, "_PACKAGE_ROOT", package_root)
    monkeypatch.delenv("BEANBEAVER_ROOT", raising=False)
    (package_root / "config" / "tui.json").write_text(
        json.dumps({"project_root": "../project"}),
        encoding="utf-8",
    )
    runtime_paths.reset_paths()

    exit_code = unified_cli.main(["api", "get-config"])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["project_root"] == "../project"
    assert captured["resolved_project_root"] == str(project_root.resolve())
    assert captured["resolved_main_beancount_path"] == str((project_root / "main.beancount").resolve())
    assert captured["scanned_dir"] == str((project_root / "receipts" / "json" / "scanned").resolve())
    assert captured["approved_dir"] == str((project_root / "receipts" / "json" / "approved").resolve())


def test_api_set_config_persists_project_root(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    package_root = tmp_path / "package"
    project_root = tmp_path / "project"
    (package_root / "config").mkdir(parents=True)
    project_root.mkdir()
    monkeypatch.setattr(runtime_paths, "_PACKAGE_ROOT", package_root)
    monkeypatch.delenv("BEANBEAVER_ROOT", raising=False)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"project_root": "../project"})),
    )
    runtime_paths.reset_paths()

    exit_code = unified_cli.main(["api", "set-config"])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["status"] == "saved"
    assert captured["project_root"] == "../project"
    assert captured["resolved_project_root"] == str(project_root.resolve())
    assert captured["resolved_main_beancount_path"] == str((project_root / "main.beancount").resolve())
    assert json.loads((package_root / "config" / "tui.json").read_text(encoding="utf-8")) == {
        "project_root": "../project"
    }
