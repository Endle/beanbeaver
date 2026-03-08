"""Helpers for the staged receipt JSON pipeline."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4

from beanbeaver.domain.receipt import Receipt, ReceiptItem, ReceiptWarning

from .date_utils import placeholder_receipt_date
from .item_categories import (
    ItemCategoryRuleLayers,
    account_for_category_key,
    classify_item_semantic,
)

SCHEMA_VERSION = "1"


def _utc_now_iso() -> str:
    """Return current UTC time in ISO-8601 format."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _decimal_to_str(value: Decimal | None) -> str | None:
    """Serialize a Decimal to a JSON-safe string."""
    if value is None:
        return None
    return f"{value:.2f}"


def _str_to_decimal(value: Any) -> Decimal | None:
    """Parse a decimal string from JSON."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return Decimal(stripped)
        except InvalidOperation:
            return None
    return None


def _date_to_iso(value: date | None) -> str | None:
    """Serialize a date to ISO-8601."""
    return value.isoformat() if value is not None else None


def _iso_to_date(value: Any) -> date | None:
    """Parse an ISO date string from JSON."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return date.fromisoformat(stripped)
        except ValueError:
            return None
    return None


def _semantic_category_from_legacy_target(
    target: str | None,
    *,
    rule_layers: ItemCategoryRuleLayers,
) -> str | None:
    """Normalize legacy account-like category targets to semantic keys."""
    if not target:
        return None
    if target in rule_layers.account_mapping:
        return target
    for key, account in rule_layers.account_mapping.items():
        if account == target:
            return key
    return None


def _account_from_classification(
    classification: dict[str, Any] | None,
    *,
    rule_layers: ItemCategoryRuleLayers,
) -> str | None:
    """Map semantic classification back to a Beancount account."""
    if not classification:
        return None

    category = classification.get("category")
    if isinstance(category, str):
        mapped = account_for_category_key(category, rule_layers.account_mapping)
        if mapped is not None:
            return mapped

    tags_raw = classification.get("tags")
    tags = [str(tag).strip().lower() for tag in tags_raw] if isinstance(tags_raw, list) else []
    for tag in tags:
        for key, mapped in rule_layers.account_mapping.items():
            if tag and tag in key.split("_"):
                return mapped

    return None


def _make_warning(message: str, *, source: str, stage: str) -> dict[str, Any]:
    """Create a structured warning payload."""
    return {
        "message": message,
        "source": source,
        "stage": stage,
    }


def build_parsed_receipt_stage(
    receipt: Receipt,
    *,
    rule_layers: ItemCategoryRuleLayers,
    raw_ocr_payload: dict[str, Any] | None = None,
    ocr_json_path: str | None = None,
    image_sha256: str | None = None,
    created_by: str = "receipt_parser",
    pass_name: str = "initial_parse",
) -> dict[str, Any]:
    """Build the initial parsed receipt stage document from a Receipt."""
    receipt_id = str(uuid4())
    stage = "parsed"
    item_docs: list[dict[str, Any]] = []
    top_level_warnings: list[dict[str, Any]] = []

    for idx, item in enumerate(receipt.items, start=1):
        semantic_category = _semantic_category_from_legacy_target(item.category, rule_layers=rule_layers)
        item_docs.append(
            {
                "id": f"item-{idx:04d}",
                "description": item.description,
                "price": _decimal_to_str(item.price),
                "quantity": item.quantity,
                "classification": classify_item_semantic(
                    item.description,
                    rule_layers,
                    default_category=semantic_category,
                ),
                "warnings": [],
                "meta": {
                    "source": "parser",
                },
            }
        )

    for warning in receipt.warnings:
        structured = _make_warning(warning.message, source="parser", stage=stage)
        warning_idx: int | None = warning.after_item_index
        if warning_idx is not None and 0 <= warning_idx < len(item_docs):
            item_docs[warning_idx].setdefault("warnings", []).append(structured)
        else:
            top_level_warnings.append(structured)

    debug: dict[str, Any] = {}
    if raw_ocr_payload is not None:
        debug["ocr_payload"] = raw_ocr_payload

    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "receipt_id": receipt_id,
        "stage": stage,
        "stage_index": 0,
        "created_at": _utc_now_iso(),
        "created_by": created_by,
        "pass_name": pass_name,
        "image_filename": receipt.image_filename or None,
        "image_sha256": image_sha256,
        "ocr_json_path": ocr_json_path,
    }

    return {
        "meta": meta,
        "receipt": {
            "merchant": receipt.merchant or None,
            "date": None if receipt.date_is_placeholder else _date_to_iso(receipt.date),
            "currency": "CAD",
            "subtotal": _decimal_to_str(receipt.subtotal),
            "tax": _decimal_to_str(receipt.tax),
            "total": _decimal_to_str(receipt.total),
        },
        "items": item_docs,
        "warnings": top_level_warnings,
        "raw_text": receipt.raw_text or None,
        "debug": debug or None,
    }


def clone_stage_document(
    document: dict[str, Any],
    *,
    stage: str,
    created_by: str,
    pass_name: str,
    parent_file: str,
) -> dict[str, Any]:
    """Create a new stage document by cloning an existing stage."""
    cloned = deepcopy(document)
    meta = dict(cloned.get("meta") or {})
    meta["stage"] = stage
    meta["stage_index"] = int(meta.get("stage_index", 0)) + 1
    meta["created_at"] = _utc_now_iso()
    meta["created_by"] = created_by
    meta["pass_name"] = pass_name
    meta["parent_file"] = parent_file
    cloned["meta"] = meta
    return cloned


def load_stage_document(path: Path) -> dict[str, Any]:
    """Load one staged receipt JSON document."""
    return json.loads(path.read_text())


def save_stage_document(path: Path, document: dict[str, Any]) -> None:
    """Persist one staged receipt JSON document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=False) + "\n")


def get_stage_index(document: dict[str, Any]) -> int:
    """Return stage_index from the document, defaulting to zero."""
    meta = document.get("meta") or {}
    try:
        return int(meta.get("stage_index", 0))
    except (TypeError, ValueError):
        return 0


def get_receipt_id(document: dict[str, Any]) -> str:
    """Return the receipt chain UUID."""
    meta = document.get("meta") or {}
    receipt_id = meta.get("receipt_id")
    return str(receipt_id) if receipt_id else ""


def _effective_receipt_value(document: dict[str, Any], key: str) -> Any:
    """Resolve one receipt-level field using review-first precedence."""
    review = document.get("review") or {}
    if key in review and review[key] is not None:
        return review[key]
    receipt_data = document.get("receipt") or {}
    return receipt_data.get(key)


def _effective_item_value(item: dict[str, Any], key: str) -> Any:
    """Resolve one item-level field using review-first precedence."""
    review = item.get("review") or {}
    if key in review and review[key] is not None:
        return review[key]
    return item.get(key)


def _effective_item_classification(item: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve item classification with partial review override support."""
    classification = deepcopy(item.get("classification") or {})
    review = item.get("review") or {}
    review_classification = review.get("classification") or {}
    if classification or review_classification:
        classification.update(review_classification)
        return classification
    return None


def get_stage_summary(document: dict[str, Any]) -> tuple[str | None, date | None, Decimal | None]:
    """Return effective merchant/date/total summary from one stage document."""
    merchant = _effective_receipt_value(document, "merchant")
    merchant_value = str(merchant) if merchant else None
    receipt_date = _iso_to_date(_effective_receipt_value(document, "date"))
    total = _str_to_decimal(_effective_receipt_value(document, "total"))
    return merchant_value, receipt_date, total


def receipt_from_stage_document(
    document: dict[str, Any],
    *,
    rule_layers: ItemCategoryRuleLayers,
) -> Receipt:
    """Resolve a staged JSON document into an effective Receipt object."""
    merchant, receipt_date, total = get_stage_summary(document)
    tax = _str_to_decimal(_effective_receipt_value(document, "tax"))
    subtotal = _str_to_decimal(_effective_receipt_value(document, "subtotal"))

    date_is_placeholder = receipt_date is None
    resolved_date = receipt_date or placeholder_receipt_date()
    resolved_total = total or Decimal("0")

    items: list[ReceiptItem] = []
    warnings: list[ReceiptWarning] = []
    active_item_index = -1

    for item in document.get("items") or []:
        if not isinstance(item, dict):
            continue
        review = item.get("review") or {}
        if bool(review.get("removed")):
            continue

        description_raw = _effective_item_value(item, "description")
        price = _str_to_decimal(_effective_item_value(item, "price"))
        quantity = _effective_item_value(item, "quantity")
        classification = _effective_item_classification(item)
        category = _account_from_classification(classification, rule_layers=rule_layers)

        description = str(description_raw).strip() if description_raw is not None else ""
        if not description:
            description = "UNKNOWN_ITEM"
        if price is None:
            price = Decimal("0")

        qty_value = quantity if isinstance(quantity, int) else 1
        items.append(
            ReceiptItem(
                description=description,
                price=price,
                quantity=qty_value,
                category=category,
            )
        )
        active_item_index += 1

        for warning_doc in item.get("warnings") or []:
            if not isinstance(warning_doc, dict):
                continue
            message = warning_doc.get("message")
            if not message:
                continue
            warnings.append(
                ReceiptWarning(
                    message=str(message),
                    after_item_index=active_item_index,
                )
            )

    for warning_doc in document.get("warnings") or []:
        if not isinstance(warning_doc, dict):
            continue
        message = warning_doc.get("message")
        if not message:
            continue
        warnings.append(
            ReceiptWarning(
                message=str(message),
                after_item_index=None,
            )
        )

    meta = document.get("meta") or {}
    return Receipt(
        merchant=merchant or "UNKNOWN_MERCHANT",
        date=resolved_date,
        date_is_placeholder=date_is_placeholder,
        total=resolved_total,
        items=items,
        tax=tax,
        subtotal=subtotal,
        raw_text=str(document.get("raw_text") or ""),
        image_filename=str(meta.get("image_filename") or ""),
        warnings=warnings,
    )
