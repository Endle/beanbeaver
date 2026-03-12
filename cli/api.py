"""Machine-readable CLI commands for external tooling such as the experimental TUI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=_json_default))


def _load_optional_stdin_json() -> dict[str, Any]:
    if sys.stdin.isatty():
        return {}
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object")
    return payload


def _resolve_stage_path(raw_path: str) -> Path:
    return Path(raw_path).expanduser().resolve()


def _receipt_summary_payload(path: Path, merchant: str | None, receipt_date: object, total: object) -> dict[str, Any]:
    return {
        "path": str(path),
        "receipt_dir": path.parent.name,
        "stage_file": path.name,
        "merchant": merchant,
        "date": _json_default(receipt_date) if receipt_date is not None else None,
        "total": _json_default(total) if total is not None else None,
    }


def cmd_api_list_scanned(args: argparse.Namespace) -> None:
    """Return scanned receipts as JSON."""
    from beanbeaver.receipt.receipt_structuring import get_stage_summary, load_stage_document
    from beanbeaver.runtime.receipt_storage import list_scanned_receipts

    receipts: list[dict[str, Any]] = []
    for path in list_scanned_receipts():
        merchant, receipt_date, total = get_stage_summary(load_stage_document(path))
        receipts.append(_receipt_summary_payload(path, merchant, receipt_date, total))

    _print_json({"receipts": receipts})


def cmd_api_list_approved(args: argparse.Namespace) -> None:
    """Return approved receipts as JSON."""
    from beanbeaver.application.receipts.listing import run_list_approved_receipts

    receipts = [
        _receipt_summary_payload(path, merchant, receipt_date, total)
        for path, merchant, receipt_date, total in run_list_approved_receipts().receipts
    ]
    _print_json({"receipts": receipts})


def cmd_api_show_receipt(args: argparse.Namespace) -> None:
    """Return one staged receipt document as JSON."""
    from beanbeaver.receipt.receipt_structuring import get_stage_summary, load_stage_document

    path = _resolve_stage_path(args.path)
    document = load_stage_document(path)
    merchant, receipt_date, total = get_stage_summary(document)
    _print_json(
        {
            "path": str(path),
            "summary": _receipt_summary_payload(path, merchant, receipt_date, total),
            "document": document,
        }
    )


def cmd_api_list_item_categories(args: argparse.Namespace) -> None:
    """Return available receipt item categories as JSON."""
    from beanbeaver.receipt.item_categories import list_item_categories
    from beanbeaver.runtime.item_category_rules import load_item_category_rule_layers

    categories = [
        {
            "key": key,
            "account": account,
        }
        for key, account in list_item_categories(load_item_category_rule_layers())
    ]
    _print_json({"categories": categories})


def cmd_api_approve_scanned(args: argparse.Namespace) -> None:
    """Approve one scanned receipt and return the new approved path."""
    from beanbeaver.application.receipts.approval import ApproveScannedReceiptRequest, run_approve_scanned_receipt

    target_path = _resolve_stage_path(args.path)
    result = run_approve_scanned_receipt(ApproveScannedReceiptRequest(target_path=target_path))
    _print_json(
        {
            "status": "approved",
            "source_path": str(target_path),
            "approved_path": str(result.approved_path),
        }
    )


def cmd_api_approve_scanned_with_review(args: argparse.Namespace) -> None:
    """Approve one scanned receipt after applying structured review overrides from stdin JSON."""
    from beanbeaver.application.receipts.approval import (
        ApproveScannedReceiptRequest,
        run_approve_scanned_receipt_with_review,
    )

    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise ValueError("Review payload must be a JSON object")

    review_patch = payload.get("review", {})
    if not isinstance(review_patch, dict):
        raise ValueError("Review payload field 'review' must be a JSON object")
    item_review_patches = payload.get("items", [])
    if not isinstance(item_review_patches, list):
        raise ValueError("Review payload field 'items' must be a JSON array")

    target_path = _resolve_stage_path(args.path)
    result = run_approve_scanned_receipt_with_review(
        ApproveScannedReceiptRequest(target_path=target_path),
        review_patch=review_patch,
        item_review_patches=item_review_patches,
    )
    _print_json(
        {
            "status": "approved",
            "source_path": str(target_path),
            "approved_path": str(result.approved_path),
        }
    )


def cmd_api_re_edit_approved_with_review(args: argparse.Namespace) -> None:
    """Update one approved receipt after applying structured review overrides from stdin JSON."""
    from beanbeaver.application.receipts.review import (
        ReEditApprovedReceiptRequest,
        run_re_edit_approved_receipt_with_review,
    )

    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise ValueError("Review payload must be a JSON object")

    review_patch = payload.get("review", {})
    if not isinstance(review_patch, dict):
        raise ValueError("Review payload field 'review' must be a JSON object")
    item_review_patches = payload.get("items", [])
    if not isinstance(item_review_patches, list):
        raise ValueError("Review payload field 'items' must be a JSON array")

    target_path = _resolve_stage_path(args.path)
    result = run_re_edit_approved_receipt_with_review(
        ReEditApprovedReceiptRequest(
            target_path=target_path,
            resolve_editor_cmd=lambda: [],
        ),
        review_patch=review_patch,
        item_review_patches=item_review_patches,
    )
    _print_json(
        {
            "status": result.status,
            "source_path": str(target_path),
            "updated_path": str(result.updated_path) if result.updated_path is not None else None,
            "normalize_error": result.normalize_error,
        }
    )


def cmd_api_match_candidates(args: argparse.Namespace) -> None:
    """Return candidate ledger matches for one approved receipt."""
    from beanbeaver.application.receipts.match import list_match_candidates_for_receipt

    target_path = _resolve_stage_path(args.path)
    result = list_match_candidates_for_receipt(target_path)
    _print_json(
        {
            "path": str(target_path),
            "ledger_path": str(result.ledger_path),
            "errors": result.errors,
            "warning": result.warning,
            "candidates": [
                {
                    "file_path": candidate.file_path,
                    "line_number": candidate.line_number,
                    "confidence": candidate.confidence,
                    "display": candidate.display,
                    "payee": candidate.payee,
                    "narration": candidate.narration,
                    "date": candidate.date,
                    "amount": candidate.amount,
                }
                for candidate in result.candidates
            ],
        }
    )


def cmd_api_apply_match(args: argparse.Namespace) -> None:
    """Apply one selected ledger match for an approved receipt from stdin JSON."""
    from beanbeaver.application.receipts.match import apply_match_for_receipt

    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise ValueError("Match payload must be a JSON object")

    candidate_file_path = payload.get("file_path")
    candidate_line_number = payload.get("line_number")
    if not isinstance(candidate_file_path, str):
        raise ValueError("Match payload field 'file_path' must be a string")
    if not isinstance(candidate_line_number, int):
        raise ValueError("Match payload field 'line_number' must be an integer")

    target_path = _resolve_stage_path(args.path)
    result = apply_match_for_receipt(
        target_path,
        candidate_file_path=candidate_file_path,
        candidate_line_number=candidate_line_number,
    )
    _print_json(
        {
            "status": result.status,
            "ledger_path": str(result.ledger_path),
            "matched_receipt_path": str(result.matched_receipt_path) if result.matched_receipt_path else None,
            "enriched_path": str(result.enriched_path) if result.enriched_path else None,
            "message": result.message,
        }
    )


def cmd_api_plan_import(args: argparse.Namespace) -> None:
    """Plan statement import from stdin JSON."""
    from beanbeaver.application.imports.service import plan_import

    payload = _load_optional_stdin_json()
    import_type = payload.get("import_type")
    csv_file = payload.get("csv_file")
    if import_type is not None and import_type not in {"cc", "chequing"}:
        raise ValueError("Import payload field 'import_type' must be 'cc' or 'chequing'")
    if csv_file is not None and not isinstance(csv_file, str):
        raise ValueError("Import payload field 'csv_file' must be a string")

    result = plan_import(
        import_type=import_type,
        csv_file=csv_file,
    )
    _print_json(
        {
            "status": result.status,
            "has_uncommitted_changes": result.has_uncommitted_changes,
            "error": result.error,
            "route": None
            if result.route is None
            else {
                "csv_file": result.route.csv_file,
                "source_path": str(result.route.source_path),
                "import_type": result.route.import_type,
                "importer_id": result.route.importer_id,
                "rule_id": result.route.rule_id,
                "stage": result.route.stage,
            },
            "route_options": [
                {
                    "csv_file": option.csv_file,
                    "source_path": str(option.source_path),
                    "import_type": option.import_type,
                    "importer_id": option.importer_id,
                    "rule_id": option.rule_id,
                    "stage": option.stage,
                }
                for option in (result.route_options or [])
            ],
        }
    )


def cmd_api_refresh_import_page(args: argparse.Namespace) -> None:
    """Return one atomic Imports-page refresh payload from stdin JSON."""
    from beanbeaver.application.imports.service import refresh_import_page

    payload = _load_optional_stdin_json()
    preferred_source_path = payload.get("preferred_source_path")
    if preferred_source_path is not None and not isinstance(preferred_source_path, str):
        raise ValueError("Import payload field 'preferred_source_path' must be a string")

    result = refresh_import_page(preferred_source_path=preferred_source_path)
    _print_json(
        {
            "planner_status": result.planner_status,
            "has_uncommitted_changes": result.has_uncommitted_changes,
            "planner_error": result.planner_error,
            "routes": [
                {
                    "csv_file": option.csv_file,
                    "source_path": str(option.source_path),
                    "import_type": option.import_type,
                    "importer_id": option.importer_id,
                    "rule_id": option.rule_id,
                    "stage": option.stage,
                }
                for option in result.routes
            ],
            "selected_source_path": result.selected_source_path,
            "account_resolution": None
            if result.account_resolution is None
            else {
                "status": result.account_resolution.status,
                "import_type": result.account_resolution.import_type,
                "csv_file": result.account_resolution.csv_file,
                "importer_id": result.account_resolution.importer_id,
                "account_label": result.account_resolution.account_label,
                "account_options": result.account_resolution.account_options,
                "as_of": result.account_resolution.as_of,
                "error": result.account_resolution.error,
            },
        }
    )


def cmd_api_resolve_import_accounts(args: argparse.Namespace) -> None:
    """Return candidate ledger accounts for one import route from stdin JSON."""
    from beanbeaver.application.imports.service import resolve_import_accounts

    payload = _load_optional_stdin_json()
    import_type = payload.get("import_type")
    csv_file = payload.get("csv_file")
    importer_id = payload.get("importer_id")
    if import_type not in {"cc", "chequing"}:
        raise ValueError("Import payload field 'import_type' must be 'cc' or 'chequing'")
    if not isinstance(csv_file, str):
        raise ValueError("Import payload field 'csv_file' must be a string")
    if importer_id is not None and not isinstance(importer_id, str):
        raise ValueError("Import payload field 'importer_id' must be a string")

    result = resolve_import_accounts(
        import_type=import_type,
        csv_file=csv_file,
        importer_id=importer_id,
    )
    _print_json(
        {
            "status": result.status,
            "import_type": result.import_type,
            "csv_file": result.csv_file,
            "importer_id": result.importer_id,
            "account_label": result.account_label,
            "account_options": result.account_options,
            "as_of": result.as_of,
            "error": result.error,
        }
    )


def cmd_api_apply_import(args: argparse.Namespace) -> None:
    """Apply one statement import from stdin JSON."""
    from beanbeaver.application.imports.service import ApplyImportRequest, apply_import

    payload = _load_optional_stdin_json()
    import_type = payload.get("import_type")
    csv_file = payload.get("csv_file")
    importer_id = payload.get("importer_id")
    selected_account = payload.get("selected_account")
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    allow_uncommitted = payload.get("allow_uncommitted")

    if import_type not in {"cc", "chequing"}:
        raise ValueError("Import payload field 'import_type' must be 'cc' or 'chequing'")
    if not isinstance(csv_file, str):
        raise ValueError("Import payload field 'csv_file' must be a string")
    if importer_id is not None and not isinstance(importer_id, str):
        raise ValueError("Import payload field 'importer_id' must be a string")
    if selected_account is not None and not isinstance(selected_account, str):
        raise ValueError("Import payload field 'selected_account' must be a string")
    if start_date is not None and not isinstance(start_date, str):
        raise ValueError("Import payload field 'start_date' must be a string")
    if end_date is not None and not isinstance(end_date, str):
        raise ValueError("Import payload field 'end_date' must be a string")
    if allow_uncommitted is not None and not isinstance(allow_uncommitted, bool):
        raise ValueError("Import payload field 'allow_uncommitted' must be a boolean")

    result = apply_import(
        ApplyImportRequest(
            import_type=import_type,
            csv_file=csv_file,
            importer_id=importer_id,
            selected_account=selected_account,
            start_date=start_date,
            end_date=end_date,
            allow_uncommitted=allow_uncommitted,
        )
    )
    _print_json(
        {
            "status": result.status,
            "import_type": result.import_type,
            "result_file_path": str(result.result_file_path) if result.result_file_path is not None else None,
            "result_file_name": result.result_file_name,
            "account": result.account,
            "start_date": result.start_date,
            "end_date": result.end_date,
            "error": result.error,
        }
    )


def cmd_api_import_apply(args: argparse.Namespace) -> None:
    """Apply one statement import with a JSON-only response."""
    from beanbeaver.application.imports.service import ApplyImportRequest, apply_import_machine_readable

    payload = _load_optional_stdin_json()
    import_type = payload.get("import_type")
    csv_file = payload.get("csv_file")
    importer_id = payload.get("importer_id")
    selected_account = payload.get("selected_account")
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    allow_uncommitted = payload.get("allow_uncommitted")

    if import_type not in {"cc", "chequing"}:
        raise ValueError("Import payload field 'import_type' must be 'cc' or 'chequing'")
    if not isinstance(csv_file, str):
        raise ValueError("Import payload field 'csv_file' must be a string")
    if importer_id is not None and not isinstance(importer_id, str):
        raise ValueError("Import payload field 'importer_id' must be a string")
    if selected_account is not None and not isinstance(selected_account, str):
        raise ValueError("Import payload field 'selected_account' must be a string")
    if start_date is not None and not isinstance(start_date, str):
        raise ValueError("Import payload field 'start_date' must be a string")
    if end_date is not None and not isinstance(end_date, str):
        raise ValueError("Import payload field 'end_date' must be a string")
    if allow_uncommitted is not None and not isinstance(allow_uncommitted, bool):
        raise ValueError("Import payload field 'allow_uncommitted' must be a boolean")

    result = apply_import_machine_readable(
        ApplyImportRequest(
            import_type=import_type,
            csv_file=csv_file,
            importer_id=importer_id,
            selected_account=selected_account,
            start_date=start_date,
            end_date=end_date,
            allow_uncommitted=allow_uncommitted,
        )
    )
    _print_json(
        {
            "status": result.status,
            "import_type": result.import_type,
            "result_file_path": str(result.result_file_path) if result.result_file_path is not None else None,
            "result_file_name": result.result_file_name,
            "account": result.account,
            "start_date": result.start_date,
            "end_date": result.end_date,
            "error": result.error,
            "warnings": list(result.warnings),
            "validation_errors": list(result.validation_errors),
            "summary": result.summary,
        }
    )


def cmd_api_get_config(args: argparse.Namespace) -> None:
    """Return TUI/backend configuration as JSON."""
    from beanbeaver.runtime import bootstrap_tui_config_path, get_paths
    from beanbeaver.runtime.tui_config import load_tui_config

    config = load_tui_config()
    paths = get_paths()
    _print_json(
        {
            "config_path": str(bootstrap_tui_config_path()),
            "project_root": config.get("project_root", ""),
            "resolved_project_root": str(paths.root),
            "resolved_main_beancount_path": str(paths.main_beancount),
            "scanned_dir": str(paths.receipts_json_scanned),
            "approved_dir": str(paths.receipts_json_approved),
        }
    )


def cmd_api_set_config(args: argparse.Namespace) -> None:
    """Persist TUI/backend configuration from stdin JSON."""
    from beanbeaver.runtime import bootstrap_tui_config_path, get_paths, reset_paths
    from beanbeaver.runtime.tui_config import set_project_root

    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise ValueError("Config payload must be a JSON object")

    project_root = payload.get("project_root", "")
    if not isinstance(project_root, str):
        raise ValueError("Config field 'project_root' must be a string")

    config_path = set_project_root(project_root)
    reset_paths()
    paths = get_paths()
    _print_json(
        {
            "status": "saved",
            "config_path": str(config_path if config_path else bootstrap_tui_config_path()),
            "project_root": project_root.strip(),
            "resolved_project_root": str(paths.root),
            "resolved_main_beancount_path": str(paths.main_beancount),
            "scanned_dir": str(paths.receipts_json_scanned),
            "approved_dir": str(paths.receipts_json_approved),
        }
    )
