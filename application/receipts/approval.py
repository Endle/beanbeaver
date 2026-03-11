"""Non-interactive receipt approval workflow orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from beanbeaver.receipt.receipt_structuring import load_stage_document, save_stage_document
from beanbeaver.runtime import load_item_category_rule_layers
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


def _normalize_decimal_text(value: object, *, label: str) -> str | None:
    text = _normalize_optional_text(value)
    if text is None:
        return None
    try:
        Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid receipt {label}: {text}") from exc
    return text


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

    if "subtotal" in review_patch:
        normalized["subtotal"] = _normalize_decimal_text(review_patch.get("subtotal"), label="subtotal")

    if "tax" in review_patch:
        normalized["tax"] = _normalize_decimal_text(review_patch.get("tax"), label="tax")

    if "total" in review_patch:
        normalized["total"] = _normalize_decimal_text(review_patch.get("total"), label="total")

    if "notes" in review_patch:
        normalized["notes"] = _normalize_optional_text(review_patch.get("notes"))

    return normalized


def _normalize_item_category(value: object) -> str | None:
    text = _normalize_optional_text(value)
    if text is None:
        return None

    if text.startswith("Expenses:"):
        rule_layers = load_item_category_rule_layers()
        inverted = {account: key for key, account in rule_layers.account_mapping.items()}
        return inverted.get(text, text)

    return text


def _validate_item_review_patches(
    item_review_patches: list[dict[str, Any]],
    *,
    known_item_ids: set[str],
) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for index, item_patch in enumerate(item_review_patches, start=1):
        if not isinstance(item_patch, dict):
            raise ValueError(f"Item review patch #{index} must be a JSON object")

        item_id = _normalize_optional_text(item_patch.get("id"))
        if item_id is None:
            raise ValueError(f"Item review patch #{index} is missing 'id'")
        if item_id not in known_item_ids:
            raise ValueError(f"Unknown item review patch id: {item_id}")

        review_patch = item_patch.get("review", {})
        if not isinstance(review_patch, dict):
            raise ValueError(f"Item review patch '{item_id}' field 'review' must be a JSON object")

        item_review: dict[str, Any] = {}
        if "description" in review_patch:
            item_review["description"] = _normalize_optional_text(review_patch.get("description"))
        if "price" in review_patch:
            item_review["price"] = _normalize_decimal_text(
                review_patch.get("price"),
                label=f"item price for '{item_id}'",
            )
        if "category" in review_patch:
            item_review["classification"] = {
                "category": _normalize_item_category(review_patch.get("category"))
            }
        if "notes" in review_patch:
            item_review["notes"] = _normalize_optional_text(review_patch.get("notes"))
        if "removed" in review_patch:
            removed = review_patch.get("removed")
            if not isinstance(removed, bool):
                raise ValueError(f"Item review patch '{item_id}' field 'removed' must be a boolean")
            item_review["removed"] = removed

        normalized[item_id] = item_review

    return normalized


def _apply_review_patches(
    document: dict[str, Any],
    *,
    review_patch: dict[str, str | None],
    item_review_patches: dict[str, dict[str, Any]],
) -> None:
    if review_patch:
        review = dict(document.get("review") or {})
        review.update(review_patch)
        document["review"] = review

    if not item_review_patches:
        return

    for item in document.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = _normalize_optional_text(item.get("id"))
        if item_id is None or item_id not in item_review_patches:
            continue

        next_review = dict(item.get("review") or {})
        patch = dict(item_review_patches[item_id])
        classification_patch = patch.pop("classification", None)
        if classification_patch is not None:
            merged_classification = dict(next_review.get("classification") or {})
            merged_classification.update(classification_patch)
            next_review["classification"] = merged_classification
        next_review.update(patch)
        item["review"] = next_review


def run_approve_scanned_receipt(request: ApproveScannedReceiptRequest) -> ApproveScannedReceiptResult:
    """Create a review stage and move a scanned receipt into approved."""
    return run_approve_scanned_receipt_with_review(request, review_patch={})


def run_approve_scanned_receipt_with_review(
    request: ApproveScannedReceiptRequest,
    *,
    review_patch: dict[str, Any],
    item_review_patches: list[dict[str, Any]] | None = None,
) -> ApproveScannedReceiptResult:
    """Create a review stage, apply receipt-level review overrides, and move to approved."""
    review_stage_path = create_next_review_stage(
        request.target_path,
        created_by="tui_review",
        pass_name="tui_approve",
    )
    normalized_patch = _validate_review_patch(review_patch)
    document = load_stage_document(review_stage_path)
    known_item_ids = {
        str(item_id).strip()
        for item_id in (item.get("id") for item in document.get("items") or [] if isinstance(item, dict))
        if str(item_id).strip()
    }
    normalized_item_patches = _validate_item_review_patches(
        item_review_patches or [],
        known_item_ids=known_item_ids,
    )
    if normalized_patch or normalized_item_patches:
        _apply_review_patches(
            document,
            review_patch=normalized_patch,
            item_review_patches=normalized_item_patches,
        )
        save_stage_document(review_stage_path, document)

    refreshed_stage_path, _ = refresh_stage_artifacts(review_stage_path)
    approved_path = move_scanned_to_approved(refreshed_stage_path)
    return ApproveScannedReceiptResult(approved_path=approved_path)
