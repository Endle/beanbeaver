"""Non-interactive receipt approval workflow orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from beanbeaver.receipt.receipt_structuring import load_stage_document, save_stage_document
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


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _validate_review_patch(review_patch: dict[str, Any]) -> dict[str, str | None]:
    normalized: dict[str, str | None] = {}

    if "merchant" in review_patch:
        normalized["merchant"] = _normalize_optional_text(review_patch.get("merchant"))

    if "date" in review_patch:
        date_value = _normalize_optional_text(review_patch.get("date"))
        if date_value is not None:
            try:
                date.fromisoformat(date_value)
            except ValueError as exc:
                raise ValueError(f"Invalid receipt date: {date_value}") from exc
        normalized["date"] = date_value

    if "total" in review_patch:
        total_value = _normalize_optional_text(review_patch.get("total"))
        if total_value is not None:
            try:
                Decimal(total_value)
            except InvalidOperation as exc:
                raise ValueError(f"Invalid receipt total: {total_value}") from exc
        normalized["total"] = total_value

    return normalized


def run_approve_scanned_receipt(request: ApproveScannedReceiptRequest) -> ApproveScannedReceiptResult:
    """Create a review stage and move a scanned receipt into approved."""
    return run_approve_scanned_receipt_with_review(request, review_patch={})


def run_approve_scanned_receipt_with_review(
    request: ApproveScannedReceiptRequest,
    *,
    review_patch: dict[str, Any],
) -> ApproveScannedReceiptResult:
    """Create a review stage, apply receipt-level review overrides, and move to approved."""
    review_stage_path = create_next_review_stage(
        request.target_path,
        created_by="tui_review",
        pass_name="tui_approve",
    )
    normalized_patch = _validate_review_patch(review_patch)
    if normalized_patch:
        document = load_stage_document(review_stage_path)
        review = dict(document.get("review") or {})
        review.update(normalized_patch)
        document["review"] = review
        save_stage_document(review_stage_path, document)

    refreshed_stage_path, _ = refresh_stage_artifacts(review_stage_path)
    approved_path = move_scanned_to_approved(refreshed_stage_path)
    return ApproveScannedReceiptResult(approved_path=approved_path)
