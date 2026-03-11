"""Parse raw OCR text into structured Receipt data."""

from datetime import date
from decimal import Decimal

from beanbeaver.domain.receipt import Receipt, ReceiptItem, ReceiptWarning

from ._rust import require_rust_matcher
from .date_utils import placeholder_receipt_date
from .item_categories import ItemCategoryRuleLayers
from .ocr_parser import (
    _extract_date,
    _extract_items,
    _extract_items_with_bbox,
    _extract_merchant,
    _extract_subtotal,
    _extract_tax,
    _extract_total,
    _has_useful_bbox_data,
    _is_spatial_layout_receipt,
)


def _legacy_parse_receipt(
    ocr_result: dict,
    item_category_rule_layers: ItemCategoryRuleLayers,
    image_filename: str = "",
    known_merchants: list[str] | tuple[str, ...] | None = None,
    reference_date: date | None = None,
) -> Receipt:
    """
    Parse OCR result into a Receipt object.

    This is a best-effort parser - results should be manually reviewed.

    Args:
        ocr_result: JSON response from OCR service with 'full_text' and 'pages'
        item_category_rule_layers: Preloaded item-category rules.
        image_filename: Source image filename for reference
        known_merchants: Optional merchant keywords loaded by runtime components.
        reference_date: Optional date anchor used to resolve ambiguous short years.

    Returns:
        Receipt object with parsed data
    """
    full_text = ocr_result.get("full_text", "")
    pages = ocr_result.get("pages", [])
    lines = [line.strip() for line in full_text.split("\n") if line.strip()]

    merchant = _extract_merchant(lines, full_text, pages, known_merchants=known_merchants)
    receipt_date = _extract_date(lines, full_text, reference_date=reference_date)
    date_is_placeholder = False
    if receipt_date is None:
        receipt_date = placeholder_receipt_date()
        date_is_placeholder = True
    total = _extract_total(lines)
    tax = _extract_tax(lines)
    subtotal = _extract_subtotal(lines)

    # Collect known summary amounts to filter from items
    summary_amounts: set[Decimal] = set()
    if total:
        summary_amounts.add(total)
    if tax:
        summary_amounts.add(tax)
    if subtotal:
        summary_amounts.add(subtotal)

    # Try bbox-based spatial parsing for receipts with items and prices on same row
    items: list[ReceiptItem] = []
    warnings: list[ReceiptWarning] = []
    if _has_useful_bbox_data(pages) and _is_spatial_layout_receipt(pages, full_text):
        items = _extract_items_with_bbox(
            pages,
            warning_sink=warnings,
            item_category_rule_layers=item_category_rule_layers,
        )

    # Fall back to text-based parsing if bbox parsing didn't find items
    if not items:
        items = _extract_items(
            lines,
            summary_amounts,
            warning_sink=warnings,
            item_category_rule_layers=item_category_rule_layers,
        )

    return Receipt(
        merchant=merchant,
        date=receipt_date,
        date_is_placeholder=date_is_placeholder,
        total=total,
        items=items,
        tax=tax,
        subtotal=subtotal,
        raw_text=full_text,
        image_filename=image_filename,
        warnings=warnings,
    )


_ORIGINAL_EXTRACT_DATE = _extract_date
_ORIGINAL_EXTRACT_ITEMS = _extract_items
_ORIGINAL_EXTRACT_ITEMS_WITH_BBOX = _extract_items_with_bbox
_ORIGINAL_EXTRACT_MERCHANT = _extract_merchant
_ORIGINAL_EXTRACT_SUBTOTAL = _extract_subtotal
_ORIGINAL_EXTRACT_TAX = _extract_tax
_ORIGINAL_EXTRACT_TOTAL = _extract_total
_ORIGINAL_HAS_USEFUL_BBOX_DATA = _has_useful_bbox_data
_ORIGINAL_IS_SPATIAL_LAYOUT_RECEIPT = _is_spatial_layout_receipt


def _helpers_are_original() -> bool:
    return (
        _extract_date is _ORIGINAL_EXTRACT_DATE
        and _extract_items is _ORIGINAL_EXTRACT_ITEMS
        and _extract_items_with_bbox is _ORIGINAL_EXTRACT_ITEMS_WITH_BBOX
        and _extract_merchant is _ORIGINAL_EXTRACT_MERCHANT
        and _extract_subtotal is _ORIGINAL_EXTRACT_SUBTOTAL
        and _extract_tax is _ORIGINAL_EXTRACT_TAX
        and _extract_total is _ORIGINAL_EXTRACT_TOTAL
        and _has_useful_bbox_data is _ORIGINAL_HAS_USEFUL_BBOX_DATA
        and _is_spatial_layout_receipt is _ORIGINAL_IS_SPATIAL_LAYOUT_RECEIPT
    )


def parse_receipt(
    ocr_result: dict,
    item_category_rule_layers: ItemCategoryRuleLayers,
    image_filename: str = "",
    known_merchants: list[str] | tuple[str, ...] | None = None,
    reference_date: date | None = None,
) -> Receipt:
    if not _helpers_are_original():
        return _legacy_parse_receipt(
            ocr_result,
            item_category_rule_layers,
            image_filename=image_filename,
            known_merchants=known_merchants,
            reference_date=reference_date,
        )

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
