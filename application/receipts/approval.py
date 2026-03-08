"""Non-interactive receipt approval workflow orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from beanbeaver.runtime.receipt_storage import (
    create_next_review_stage,
    move_scanned_to_approved,
    refresh_stage_artifacts,
)


@dataclass(frozen=True)
class ApproveScannedReceiptRequest:
    """Inputs for approving one scanned receipt without launching an editor."""

    target_path: Path


@dataclass(frozen=True)
class ApproveScannedReceiptResult:
    """Outcome for approving one scanned receipt without launching an editor."""

    approved_path: Path


def run_approve_scanned_receipt(request: ApproveScannedReceiptRequest) -> ApproveScannedReceiptResult:
    """Create a review stage and move a scanned receipt into approved."""
    review_stage_path = create_next_review_stage(
        request.target_path,
        created_by="tui_review",
        pass_name="tui_approve",
    )
    refreshed_stage_path, _ = refresh_stage_artifacts(review_stage_path)
    approved_path = move_scanned_to_approved(refreshed_stage_path)
    return ApproveScannedReceiptResult(approved_path=approved_path)
