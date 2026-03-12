"""Parse raw OCR text into structured Receipt data."""

from datetime import date
from decimal import Decimal

from beanbeaver.domain.receipt import Receipt, ReceiptItem, ReceiptWarning

from ._rust import require_rust_matcher
from .date_utils import placeholder_receipt_date
from .item_categories import ItemCategoryRuleLayers


def parse_receipt(
    ocr_result: dict,
    item_category_rule_layers: ItemCategoryRuleLayers,
    image_filename: str = "",
    known_merchants: list[str] | tuple[str, ...] | None = None,
    reference_date: date | None = None,
) -> Receipt:
    native = require_rust_matcher().receipt_parse_receipt(
        ocr_result,
        item_category_rule_layers,
        image_filename,
        list(known_merchants) if known_merchants is not None else None,
        (reference_date or date.today()).year,
    )
    resolved_date = native.get("date")
    receipt_date = (
        date(int(resolved_date[0]), int(resolved_date[1]), int(resolved_date[2]))
        if resolved_date is not None
        else placeholder_receipt_date()
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
    )
