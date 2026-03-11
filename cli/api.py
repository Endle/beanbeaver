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
    """Update one approved receipt after applying receipt-level review overrides from stdin JSON."""
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

    target_path = _resolve_stage_path(args.path)
    result = run_re_edit_approved_receipt_with_review(
        ReEditApprovedReceiptRequest(
            target_path=target_path,
            resolve_editor_cmd=lambda: [],
        ),
        review_patch=review_patch,
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
