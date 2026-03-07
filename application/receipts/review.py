"""Receipt review workflow orchestration."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from beanbeaver.runtime.receipt_storage import (
    create_next_review_stage,
    move_scanned_to_approved,
    refresh_stage_artifacts,
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
    review_stage_path = create_next_review_stage(request.target_path)
    editor_cmd = request.resolve_editor_cmd()
    try:
        result = subprocess.run(editor_cmd + [str(review_stage_path)])
    except FileNotFoundError:
        if review_stage_path.exists():
            review_stage_path.unlink()
        return EditScannedReceiptResult(
            status="editor_not_found",
            editor_cmd=editor_cmd,
        )

    if result.returncode != 0:
        if review_stage_path.exists():
            review_stage_path.unlink()
        return EditScannedReceiptResult(
            status="editor_failed",
            editor_returncode=result.returncode,
        )

    if not review_stage_path.exists():
        return EditScannedReceiptResult(status="edited_file_missing")

    refreshed_stage_path, _ = refresh_stage_artifacts(review_stage_path)
    approved_path = move_scanned_to_approved(refreshed_stage_path)
    return EditScannedReceiptResult(
        status="staged",
        approved_path=approved_path,
    )


def run_re_edit_approved_receipt(request: ReEditApprovedReceiptRequest) -> ReEditApprovedReceiptResult:
    """Re-edit one approved receipt and normalize filename based on edited content."""
    review_stage_path = create_next_review_stage(request.target_path)
    editor_cmd = request.resolve_editor_cmd()
    try:
        result = subprocess.run(editor_cmd + [str(review_stage_path)])
    except FileNotFoundError:
        if review_stage_path.exists():
            review_stage_path.unlink()
        return ReEditApprovedReceiptResult(
            status="editor_not_found",
            editor_cmd=editor_cmd,
        )

    if result.returncode != 0:
        if review_stage_path.exists():
            review_stage_path.unlink()
        return ReEditApprovedReceiptResult(
            status="editor_failed",
            editor_returncode=result.returncode,
        )

    if not review_stage_path.exists():
        return ReEditApprovedReceiptResult(status="edited_file_missing")

    try:
        normalized_stage_path, _ = refresh_stage_artifacts(review_stage_path)
    except Exception as exc:
        return ReEditApprovedReceiptResult(
            status="normalize_failed",
            normalize_error=str(exc),
        )

    return ReEditApprovedReceiptResult(
        status="updated",
        updated_path=normalized_stage_path,
    )
