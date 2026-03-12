"""Helpers for the staged receipt JSON pipeline."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4

from beanbeaver.domain.receipt import Receipt, ReceiptItem, ReceiptWarning

from ._rust import require_rust_matcher
from .date_utils import placeholder_receipt_date
from .item_categories import ItemCategoryRuleLayers

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
    return require_rust_matcher().receipt_build_parsed_receipt_stage(
        receipt,
        rule_layers,
        raw_ocr_payload,
        ocr_json_path,
        image_sha256,
        created_by,
        pass_name,
        _utc_now_iso(),
        str(uuid4()),
    )


def clone_stage_document(
    document: dict[str, Any],
    *,
    stage: str,
    created_by: str,
    pass_name: str,
    parent_file: str,
) -> dict[str, Any]:
    """Create a new stage document by cloning an existing stage."""
    return require_rust_matcher().receipt_clone_stage_document(
        document,
        stage,
        created_by,
        pass_name,
        parent_file,
        _utc_now_iso(),
    )


def load_stage_document(path: Path) -> dict[str, Any]:
    """Load one staged receipt JSON document."""
    return json.loads(path.read_text())


def save_stage_document(path: Path, document: dict[str, Any]) -> None:
    """Persist one staged receipt JSON document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=False) + "\n")


def get_stage_index(document: dict[str, Any]) -> int:
    """Return stage_index from the document, defaulting to zero."""
    return int(require_rust_matcher().receipt_get_stage_index(document))


def get_receipt_id(document: dict[str, Any]) -> str:
    """Return the receipt chain UUID."""
    return str(require_rust_matcher().receipt_get_receipt_id(document))


def get_stage_summary(document: dict[str, Any]) -> tuple[str | None, date | None, Decimal | None]:
    """Return effective merchant/date/total summary from one stage document."""
    merchant_value, receipt_date_iso, total_value = require_rust_matcher().receipt_get_stage_summary(document)
    return merchant_value, _iso_to_date(receipt_date_iso), _str_to_decimal(total_value)


def receipt_from_stage_document(
    document: dict[str, Any],
    *,
    rule_layers: ItemCategoryRuleLayers,
) -> Receipt:
    """Resolve a staged JSON document into an effective Receipt object."""
    resolved = require_rust_matcher().receipt_resolve_stage_document(document, rule_layers)
    resolved_date = _iso_to_date(resolved.get("date")) or placeholder_receipt_date()
    resolved_total = _str_to_decimal(resolved.get("total")) or Decimal("0")

    items = [
        ReceiptItem(
            description=description,
            price=_str_to_decimal(price) or Decimal("0"),
            quantity=quantity,
            category=category,
        )
        for description, price, quantity, category in resolved.get("items", [])
    ]
    warnings = [
        ReceiptWarning(
            message=message,
            after_item_index=after_item_index,
        )
        for message, after_item_index in resolved.get("warnings", [])
    ]

    return Receipt(
        merchant=str(resolved.get("merchant") or "UNKNOWN_MERCHANT"),
        date=resolved_date,
        date_is_placeholder=bool(resolved.get("date_is_placeholder")),
        total=resolved_total,
        items=items,
        tax=_str_to_decimal(resolved.get("tax")),
        subtotal=_str_to_decimal(resolved.get("subtotal")),
        raw_text=str(resolved.get("raw_text") or ""),
        image_filename=str(resolved.get("image_filename") or ""),
        warnings=warnings,
    )
