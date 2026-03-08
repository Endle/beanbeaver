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
    """Approve one scanned receipt after applying receipt-level review overrides from stdin JSON."""
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

    target_path = _resolve_stage_path(args.path)
    result = run_approve_scanned_receipt_with_review(
        ApproveScannedReceiptRequest(target_path=target_path),
        review_patch=review_patch,
    )
    _print_json(
        {
            "status": "approved",
            "source_path": str(target_path),
            "approved_path": str(result.approved_path),
        }
    )


def cmd_api_get_config(args: argparse.Namespace) -> None:
    """Return TUI/backend configuration as JSON."""
    from beanbeaver.runtime import get_paths
    from beanbeaver.runtime.tui_config import load_tui_config

    config = load_tui_config()
    _print_json(
        {
            "config_path": str(get_paths().config / "tui.json"),
            "main_beancount_path": config.get("main_beancount_path", ""),
            "resolved_main_beancount_path": str(get_paths().main_beancount),
        }
    )


def cmd_api_set_config(args: argparse.Namespace) -> None:
    """Persist TUI/backend configuration from stdin JSON."""
    from beanbeaver.runtime import get_paths
    from beanbeaver.runtime.tui_config import set_main_beancount_path

    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise ValueError("Config payload must be a JSON object")

    main_beancount_path = payload.get("main_beancount_path", "")
    if not isinstance(main_beancount_path, str):
        raise ValueError("Config field 'main_beancount_path' must be a string")

    config_path = set_main_beancount_path(main_beancount_path)
    _print_json(
        {
            "status": "saved",
            "config_path": str(config_path),
            "main_beancount_path": main_beancount_path.strip(),
            "resolved_main_beancount_path": str(get_paths().main_beancount),
        }
    )
