"""Tests for machine-readable `bb api` commands."""

from __future__ import annotations

import importlib
import io
import json
from pathlib import Path

import beanbeaver.application.imports.chequing as chequing_import
import beanbeaver.application.imports.credit_card as credit_card_import
import beanbeaver.application.imports.service as import_service
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
    importlib.reload(credit_card_import)
    importlib.reload(chequing_import)
    importlib.reload(import_service)
    return paths


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _canonical_stage_path(paths: ProjectPaths, *, receipt_dir: str, filename: str) -> Path:
    return paths.receipts / receipt_dir / "stages" / filename


def test_api_list_scanned_returns_json(tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    stage_path = _canonical_stage_path(
        paths,
        receipt_dir="2026-03-01_store_12_34_abcd",
        filename="000_parsed.receipt.json",
    )
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
    assert len(captured["receipts"]) == 1
    receipt = captured["receipts"][0]
    assert receipt["date"] == "2026-03-01"
    assert receipt["merchant"] == "Store"
    assert receipt["stage_file"] == "000_parsed.receipt.json"
    assert receipt["total"] == "12.34"
    assert receipt["receipt_dir"] == "2026-03-01_store_12_34_abcd"
    assert receipt["path"] == str(stage_path)


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

    stage_path = _canonical_stage_path(
        paths,
        receipt_dir="2026-03-01_store_12_34_abcd",
        filename="000_parsed.receipt.json",
    )
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
    stage_path = _canonical_stage_path(
        paths,
        receipt_dir="2026-03-02_shop_30_00_beef",
        filename="010_review.receipt.json",
    )
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
        "stage_file": "010_review.receipt.json",
        "total": "30.00",
    }
    assert captured["document"] == document


def test_api_list_item_categories_returns_category_options(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    _configure_temp_root(tmp_path, monkeypatch)

    exit_code = unified_cli.main(["api", "list-item-categories"])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert any(
        category == {
            "key": "grocery_dairy",
            "account": "Expenses:Food:Grocery:Dairy",
        }
        for category in captured["categories"]
    )


def test_api_approve_scanned_moves_receipt_and_creates_review_stage(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    stage_path = _canonical_stage_path(
        paths,
        receipt_dir="2026-03-03_market_8_50_cafe",
        filename="000_parsed.receipt.json",
    )
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
    assert approved_path.parent.name == "stages"
    assert approved_path.parent.parent.parent == paths.receipts
    assert approved_path.name == "010_review.receipt.json"
    assert approved_document["meta"]["stage_index"] == 1
    assert approved_document["meta"]["created_by"] == "tui_review"
    assert approved_document["meta"]["pass_name"] == "tui_approve"


def test_api_approve_scanned_with_review_applies_receipt_overrides(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    stage_path = _canonical_stage_path(
        paths,
        receipt_dir="2026-03-04_market_10_00_feed",
        filename="000_parsed.receipt.json",
    )
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


def test_api_approve_scanned_with_review_applies_item_overrides(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    stage_path = _canonical_stage_path(
        paths,
        receipt_dir="2026-03-04_costco_221_97_feed",
        filename="000_parsed.receipt.json",
    )
    save_stage_document(
        stage_path,
        {
            **_stage_document(
                merchant="COSTCO",
                receipt_date="2026-03-04",
                total="221.97",
                stage="parsed",
                stage_index=0,
            ),
            "items": [
                {
                    "id": "item-0001",
                    "description": "COKE ZERO",
                    "price": "17.19",
                    "quantity": 1,
                    "classification": {"category": "grocery_drink_cocacola"},
                    "warnings": [],
                    "meta": {"source": "parser"},
                },
                {
                    "id": "item-0002",
                    "description": "DOORDASH2X50",
                    "price": "79.99",
                    "quantity": 1,
                    "classification": {"category": "restaurant_gift_card"},
                    "warnings": [],
                    "meta": {"source": "parser"},
                },
            ],
        },
    )
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps(
                {
                    "review": {"notes": "Verified against Costco receipt"},
                    "items": [
                        {
                            "id": "item-0001",
                            "review": {
                                "description": "COKE ZERO 35PK",
                                "price": "18.49",
                                "notes": "Promo pack confirmed in warehouse",
                                "category": "Expenses:Food:Grocery:Drink:CocaCola",
                            },
                        },
                        {
                            "id": "item-0002",
                            "review": {
                                "removed": True,
                            },
                        },
                    ],
                }
            )
        ),
    )

    exit_code = unified_cli.main(["api", "approve-scanned-with-review", str(stage_path)])

    captured = json.loads(capsys.readouterr().out)
    approved_path = Path(captured["approved_path"])
    approved_document = json.loads(approved_path.read_text())
    assert exit_code == 0
    assert approved_document["review"]["notes"] == "Verified against Costco receipt"
    assert approved_document["items"][0]["review"] == {
        "description": "COKE ZERO 35PK",
        "price": "18.49",
        "notes": "Promo pack confirmed in warehouse",
        "classification": {"category": "grocery_drink_cocacola"},
    }
    assert approved_document["items"][1]["review"] == {
        "removed": True,
    }


def test_api_re_edit_approved_with_review_applies_receipt_overrides(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    stage_path = _canonical_stage_path(
        paths,
        receipt_dir="2026-03-04_market_10_00_feed",
        filename="010_review.receipt.json",
    )
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


def test_api_re_edit_approved_with_review_appends_new_item(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    stage_path = _canonical_stage_path(
        paths,
        receipt_dir="2026-03-04_market_10_00_feed",
        filename="010_review.receipt.json",
    )
    save_stage_document(
        stage_path,
        {
            **_stage_document(
                merchant="Market",
                receipt_date="2026-03-04",
                total="10.00",
                stage="review_stage_1",
                stage_index=1,
            ),
            "items": [
                {
                    "id": "item-0001",
                    "description": "MILK",
                    "price": "4.99",
                    "quantity": 1,
                    "classification": {"category": "grocery_drink_cocacola"},
                    "warnings": [],
                    "meta": {"source": "parser"},
                }
            ],
        },
    )
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps(
                {
                    "items": [
                        {
                            "id": "item-added-0001",
                            "create": True,
                            "review": {
                                "description": "BANANAS",
                                "price": "3.99",
                                "notes": "manually added from receipt edge",
                                "category": "grocery_drink_cocacola",
                            },
                        }
                    ]
                }
            )
        ),
    )

    exit_code = unified_cli.main(["api", "re-edit-approved-with-review", str(stage_path)])

    captured = json.loads(capsys.readouterr().out)
    updated_path = Path(captured["updated_path"])
    updated_document = json.loads(updated_path.read_text())
    assert exit_code == 0
    assert captured["status"] == "updated"
    assert updated_document["items"][-1] == {
        "id": "item-added-0001",
        "description": "BANANAS",
        "price": "3.99",
        "quantity": 1,
        "classification": {"category": "grocery_drink_cocacola"},
        "notes": "manually added from receipt edge",
        "warnings": [],
        "meta": {"source": "tui_review"},
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
    stage_path = _canonical_stage_path(
        paths,
        receipt_dir="2026-03-04_market_10_00_feed",
        filename="010_review.receipt.json",
    )
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
    stage_path = _canonical_stage_path(
        paths,
        receipt_dir="2026-03-04_market_10_00_feed",
        filename="010_review.receipt.json",
    )
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


def test_api_plan_import_lists_multiple_route_options(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    _configure_temp_root(tmp_path, monkeypatch)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(downloads))
    runtime_paths.reset_paths()
    importlib.reload(credit_card_import)
    importlib.reload(chequing_import)
    importlib.reload(import_service)

    (downloads / "statement.csv").write_text(
        "ignored line 1\nignored line 2\nTransaction Date,Description,Transaction Amount\n20260304,Market,10.00\n",
        encoding="utf-8",
    )
    (downloads / "Preferred_Package_foo.csv").write_text(
        "Date,Description,Type of Transaction,Sub-description,Amount,Balance\n2026-03-04,Deposit,Deposit,,100.00,100.00\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    exit_code = unified_cli.main(["api", "plan-import"])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["status"] == "needs_selection"
    assert captured["error"] is None
    assert len(captured["route_options"]) == 2
    assert {option["import_type"] for option in captured["route_options"]} == {"cc", "chequing"}
    assert {option["csv_file"] for option in captured["route_options"]} == {
        "statement.csv",
        "Preferred_Package_foo.csv",
    }
    assert captured["has_uncommitted_changes"] is False


def test_api_refresh_import_page_returns_routes_and_selected_account_resolution(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(downloads))
    runtime_paths.reset_paths()
    importlib.reload(credit_card_import)
    importlib.reload(chequing_import)
    importlib.reload(import_service)

    paths.records_current_year.mkdir(parents=True, exist_ok=True)
    _write(
        paths.main_beancount,
        """
option "operating_currency" "CAD"
2026-01-01 open Liabilities:CreditCard:Primary:BMO:CardA CAD
2026-01-01 open Assets:CA:EQBank:Chequing CAD
""".lstrip(),
    )
    statement_path = downloads / "statement.csv"
    statement_path.write_text(
        "ignored line 1\nignored line 2\nTransaction Date,Description,Transaction Amount\n20260304,Market,10.00\n",
        encoding="utf-8",
    )
    (downloads / "EQ Bank Details.csv").write_text(
        "Transfer Date,Description,Amount,Balance\n2026-03-04,Deposit,100.00,100.00\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"preferred_source_path": str(statement_path)})),
    )
    exit_code = unified_cli.main(["api", "refresh-import-page"])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["planner_status"] == "needs_selection"
    assert captured["planner_error"] is None
    assert len(captured["routes"]) == 2
    assert captured["selected_source_path"] == str(statement_path)
    assert captured["account_resolution"]["status"] == "ready"
    assert captured["account_resolution"]["import_type"] == "cc"
    assert captured["account_resolution"]["importer_id"] == "bmo"
    assert captured["account_resolution"]["account_label"] == "BMO credit card"
    assert captured["account_resolution"]["account_options"] == [
        "Liabilities:CreditCard:Primary:BMO:CardA"
    ]
    assert captured["account_resolution"]["as_of"] == "2026-03-04"


def test_api_resolve_import_accounts_for_credit_card_route(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(downloads))
    runtime_paths.reset_paths()
    importlib.reload(credit_card_import)
    importlib.reload(chequing_import)
    importlib.reload(import_service)

    paths.records_current_year.mkdir(parents=True, exist_ok=True)
    _write(
        paths.main_beancount,
        """
option "operating_currency" "CAD"
2026-01-01 open Liabilities:CreditCard:Primary:BMO:CardA CAD
2026-01-01 open Liabilities:CreditCard:Travel:Porter CAD
""".lstrip(),
    )
    (downloads / "statement.csv").write_text(
        "ignored line 1\nignored line 2\nTransaction Date,Description,Transaction Amount\n20260304,Market,10.00\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"import_type": "cc", "csv_file": "statement.csv", "importer_id": "bmo"})),
    )
    exit_code = unified_cli.main(["api", "resolve-import-accounts"])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["status"] == "ready"
    assert captured["import_type"] == "cc"
    assert captured["importer_id"] == "bmo"
    assert captured["account_label"] == "BMO credit card"
    assert captured["account_options"] == ["Liabilities:CreditCard:Primary:BMO:CardA"]
    assert captured["as_of"] == "2026-03-04"


def test_api_apply_import_credit_card_with_selected_account(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(downloads))
    runtime_paths.reset_paths()
    importlib.reload(credit_card_import)
    importlib.reload(chequing_import)
    importlib.reload(import_service)

    paths.records_current_year.mkdir(parents=True, exist_ok=True)
    _write(
        paths.main_beancount,
        """
option "operating_currency" "CAD"
2026-01-01 open Liabilities:CreditCard:Primary:BMO:CardA CAD
""".lstrip(),
    )
    (downloads / "statement.csv").write_text(
        "ignored line 1\nignored line 2\nTransaction Date,Description,Transaction Amount\n20260304,Market,10.00\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps(
                {
                    "import_type": "cc",
                    "csv_file": "statement.csv",
                    "importer_id": "bmo",
                    "selected_account": "Liabilities:CreditCard:Primary:BMO:CardA",
                    "allow_uncommitted": True,
                }
            )
        ),
    )
    exit_code = unified_cli.main(["api", "apply-import"])

    captured = json.loads(capsys.readouterr().out)
    result_path = Path(captured["result_file_path"])
    assert exit_code == 0
    assert captured["status"] == "ok"
    assert captured["account"] == "Liabilities:CreditCard:Primary:BMO:CardA"
    assert captured["start_date"] == "0304"
    assert captured["end_date"] == "0304"
    assert result_path.exists()
    assert 'include "' + result_path.name + '"' in paths.yearly_summary.read_text(encoding="utf-8")


def test_api_import_apply_chequing_returns_json_only_with_validation_details(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    paths = _configure_temp_root(tmp_path, monkeypatch)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(downloads))
    runtime_paths.reset_paths()
    importlib.reload(credit_card_import)
    importlib.reload(chequing_import)
    importlib.reload(import_service)

    paths.records_current_year.mkdir(parents=True, exist_ok=True)
    _write(
        paths.main_beancount,
        """
option "operating_currency" "CAD"
2026-01-01 open Assets:Bank:Chequing:Scotia:Primary CAD
2026-01-01 open Expenses:Uncategorized CAD
""".lstrip(),
    )
    _write(
        paths.chequing_rules,
        """
[[rules]]
pattern = "DEPOSIT"
account = "Income:Unknown"
""".lstrip(),
    )
    (downloads / "Preferred_Package_foo.csv").write_text(
        "Date,Description,Type of Transaction,Sub-description,Amount,Balance\n"
        "2026-03-04,Deposit,Deposit,,100.00,100.00\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        chequing_import,
        "validate_ledger",
        lambda ledger_path: ["Validation error 1", "Validation error 2"],
    )
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps(
                {
                    "import_type": "chequing",
                    "csv_file": "Preferred_Package_foo.csv",
                    "selected_account": "Assets:Bank:Chequing:Scotia:Primary",
                    "allow_uncommitted": True,
                }
            )
        ),
    )

    exit_code = unified_cli.main(["api", "import-apply"])

    captured = json.loads(capsys.readouterr().out)
    result_path = Path(captured["result_file_path"])
    assert exit_code == 0
    assert captured["status"] == "ok"
    assert captured["account"] == "Assets:Bank:Chequing:Scotia:Primary"
    assert captured["warnings"] == ["Ledger validation found errors after import."]
    assert captured["validation_errors"] == ["Validation error 1", "Validation error 2"]
    assert captured["summary"].startswith("Import complete: ")
    assert result_path.exists()


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
    assert captured["receipts_dir"] == str((project_root / "receipts").resolve())


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
    assert captured["receipts_dir"] == str((project_root / "receipts").resolve())
    assert json.loads((package_root / "config" / "tui.json").read_text(encoding="utf-8")) == {
        "project_root": "../project"
    }
