"""Render staged receipt JSON documents as Beancount."""

from __future__ import annotations

from typing import Any

from beanbeaver.receipt.receipt_structuring import get_stage_summary, receipt_from_stage_document

from ..item_categories import ItemCategoryRuleLayers
from ..formatter import format_parsed_receipt


def render_stage_document_as_beancount(
    document: dict[str, Any],
    *,
    rule_layers: ItemCategoryRuleLayers,
    credit_card_account: str = "Liabilities:CreditCard:PENDING",
) -> str:
    """Render one staged receipt JSON document as Beancount."""
    _, _, total = get_stage_summary(document)
    if total is None:
        raise ValueError("receipt total is missing")

    receipt = receipt_from_stage_document(document, rule_layers=rule_layers)
    meta = document.get("meta") or {}
    image_sha256 = meta.get("image_sha256")
    return format_parsed_receipt(
        receipt,
        credit_card_account=credit_card_account,
        image_sha256=str(image_sha256) if image_sha256 else None,
    )
