"""Parse raw OCR text into structured Receipt data."""

from datetime import date
from decimal import Decimal
from typing import Any

from beanbeaver.domain.receipt import Receipt, ReceiptItem, ReceiptWarning, Tender, TenderKind

from ._rust import require_rust_matcher
from .date_utils import placeholder_receipt_date
from .item_categories import ItemCategoryRuleLayers
from .ocr_schema import OcrDocument


def _native_to_receipt(native: dict[str, Any]) -> Receipt:
    """Map the Rust parser's output dict into a domain :class:`Receipt`."""
    resolved_date = native.get("date")
    receipt_date = (
        date(int(resolved_date[0]), int(resolved_date[1]), int(resolved_date[2]))
        if resolved_date is not None
        else placeholder_receipt_date()
    )
    valid_tender_kinds = {"card", "gift_card", "cash", "store_credit"}
    tenders: list[Tender] = []
    for amount_str, account, kind, raw_label in native.get("tenders", []):
        normalized_kind: TenderKind = kind if kind in valid_tender_kinds else "card"
        tenders.append(
            Tender(
                amount=Decimal(str(amount_str)),
                account=account if account else None,
                kind=normalized_kind,
                raw_label=str(raw_label or ""),
            )
        )

    return Receipt(
        merchant=str(native.get("merchant") or "UNKNOWN_MERCHANT"),
        date=receipt_date,
        date_is_placeholder=bool(native.get("date_is_placeholder")),
        total=Decimal(str(native.get("total"))),
        items=[
            ReceiptItem(
                description=description,
                price=Decimal(str(price)),
                quantity=quantity,
                category=category,
            )
            for description, price, quantity, category in native.get("items", [])
        ],
        tax=Decimal(str(native.get("tax"))) if native.get("tax") is not None else None,
        subtotal=Decimal(str(native.get("subtotal"))) if native.get("subtotal") is not None else None,
        raw_text=str(native.get("raw_text") or ""),
        image_filename=str(native.get("image_filename") or ""),
        warnings=[
            ReceiptWarning(message=message, after_item_index=after_item_index)
            for message, after_item_index in native.get("warnings", [])
        ],
        tenders=tenders,
    )


def parse_receipt(
    ocr_result: OcrDocument | dict[str, Any],
    item_category_rule_layers: ItemCategoryRuleLayers,
    image_filename: str = "",
    known_merchants: list[str] | tuple[str, ...] | None = None,
    reference_date: date | None = None,
) -> Receipt:
    """Parse an already-transformed OCR document (full_text + pages) into a Receipt."""
    native = require_rust_matcher().receipt_parse_receipt(
        ocr_result,
        item_category_rule_layers,
        image_filename,
        list(known_merchants) if known_merchants is not None else None,
        (reference_date or date.today()).year,
    )
    return _native_to_receipt(native)


def parse_receipt_from_raw(
    raw_result: dict[str, Any],
    item_category_rule_layers: ItemCategoryRuleLayers,
    image_filename: str = "",
    known_merchants: list[str] | tuple[str, ...] | None = None,
    reference_date: date | None = None,
) -> Receipt:
    """Parse a raw OCR result (``{image_width, image_height, detections}``) directly.

    The detection→parser transform runs in Rust (`receipt-core`), so this needs no
    Python ``transform_paddleocr_result`` step — the desktop live path, unified
    with the iOS pipeline.
    """
    native = require_rust_matcher().receipt_parse_receipt_from_raw(
        raw_result,
        item_category_rule_layers,
        image_filename,
        list(known_merchants) if known_merchants is not None else None,
        (reference_date or date.today()).year,
    )
    return _native_to_receipt(native)
