"""Parse raw OCR text into structured Receipt data."""

from decimal import Decimal

from beanbeaver.domain.receipt import Receipt, ReceiptItem, ReceiptWarning

from .date_utils import placeholder_receipt_date
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


def parse_receipt(
    ocr_result: dict,
    image_filename: str = "",
    known_merchants: list[str] | tuple[str, ...] | None = None,
) -> Receipt:
    """
    Parse OCR result into a Receipt object.

    This is a best-effort parser - results should be manually reviewed.

    Args:
        ocr_result: JSON response from OCR service with 'full_text' and 'pages'
        image_filename: Source image filename for reference
        known_merchants: Optional merchant keywords loaded by runtime components.

    Returns:
        Receipt object with parsed data
    """
    full_text = ocr_result.get("full_text", "")
    pages = ocr_result.get("pages", [])
    lines = [line.strip() for line in full_text.split("\n") if line.strip()]

    merchant = _extract_merchant(lines, full_text, pages, known_merchants=known_merchants)
    receipt_date = _extract_date(lines, full_text)
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
        items = _extract_items_with_bbox(pages, warning_sink=warnings)

    # Fall back to text-based parsing if bbox parsing didn't find items
    if not items:
        items = _extract_items(lines, summary_amounts, warning_sink=warnings)

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
