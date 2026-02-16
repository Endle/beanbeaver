"""Receipt review workflow orchestration."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from beanbeaver.runtime.receipt_storage import (
    generate_receipt_filename,
    move_scanned_to_approved,
    parse_receipt_from_beancount,
)

EditScannedStatus = Literal[
    "editor_not_found",
    "editor_failed",
    "edited_file_missing",
    "staged",
]

ReEditApprovedStatus = Literal[
    "editor_not_found",
    "editor_failed",
    "edited_file_missing",
    "normalize_failed",
    "updated",
]


@dataclass(frozen=True)
class EditScannedReceiptRequest:
    """Inputs for editing one scanned receipt."""

    target_path: Path
    resolve_editor_cmd: Callable[[], list[str]]


@dataclass(frozen=True)
class EditScannedReceiptResult:
    """Outcome for editing one scanned receipt."""

    status: EditScannedStatus
    approved_path: Path | None = None
    editor_cmd: list[str] | None = None
    editor_returncode: int | None = None


@dataclass(frozen=True)
class ReEditApprovedReceiptRequest:
    """Inputs for re-editing one approved receipt."""

    target_path: Path
    resolve_editor_cmd: Callable[[], list[str]]


@dataclass(frozen=True)
class ReEditApprovedReceiptResult:
    """Outcome for re-editing one approved receipt."""

    status: ReEditApprovedStatus
    updated_path: Path | None = None
    normalize_error: str | None = None
    editor_cmd: list[str] | None = None
    editor_returncode: int | None = None


def run_edit_scanned_receipt(request: EditScannedReceiptRequest) -> EditScannedReceiptResult:
    """Edit one scanned receipt and stage it to approved when successful."""
    editor_cmd = request.resolve_editor_cmd()
    try:
        result = subprocess.run(editor_cmd + [str(request.target_path)])
    except FileNotFoundError:
        return EditScannedReceiptResult(
            status="editor_not_found",
            editor_cmd=editor_cmd,
        )

    if result.returncode != 0:
        return EditScannedReceiptResult(
            status="editor_failed",
            editor_returncode=result.returncode,
        )

    if not request.target_path.exists():
        return EditScannedReceiptResult(status="edited_file_missing")

    approved_path = move_scanned_to_approved(request.target_path)
    return EditScannedReceiptResult(
        status="staged",
        approved_path=approved_path,
    )


def run_re_edit_approved_receipt(request: ReEditApprovedReceiptRequest) -> ReEditApprovedReceiptResult:
    """Re-edit one approved receipt and normalize filename based on edited content."""
    editor_cmd = request.resolve_editor_cmd()
    try:
        result = subprocess.run(editor_cmd + [str(request.target_path)])
    except FileNotFoundError:
        return ReEditApprovedReceiptResult(
            status="editor_not_found",
            editor_cmd=editor_cmd,
        )

    if result.returncode != 0:
        return ReEditApprovedReceiptResult(
            status="editor_failed",
            editor_returncode=result.returncode,
        )

    if not request.target_path.exists():
        return ReEditApprovedReceiptResult(status="edited_file_missing")

    try:
        receipt = parse_receipt_from_beancount(request.target_path)
        canonical_name = generate_receipt_filename(receipt)
    except Exception as exc:
        return ReEditApprovedReceiptResult(
            status="normalize_failed",
            normalize_error=str(exc),
        )

    approved_dir = request.target_path.parent
    canonical_path = approved_dir / canonical_name
    final_path = canonical_path
    if final_path.exists() and final_path != request.target_path:
        counter = 1
        base = canonical_path.stem
        while final_path.exists():
            final_path = approved_dir / f"{base}_{counter}.beancount"
            counter += 1

    if final_path != request.target_path:
        request.target_path.rename(final_path)

    return ReEditApprovedReceiptResult(
        status="updated",
        updated_path=final_path,
    )

