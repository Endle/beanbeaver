"""Data models for receipt scanning."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class ReceiptItem:
    """A single line item on a receipt."""

    description: str
    price: Decimal
    quantity: int = 1
    category: str | None = None  # e.g., "Expenses:Food:Grocery:Dairy"

    @property
    def total(self) -> Decimal:
        # Price is the authoritative line total from the receipt.
        # Quantity is informational only and should not be used to compute totals.
        return self.price


@dataclass
class ReceiptWarning:
    """Parser warning attached to a nearby item position."""

    message: str
    # Insert warning after this item index when formatting. None means no anchor.
    after_item_index: int | None = None


@dataclass
class Receipt:
    """Parsed receipt data."""

    merchant: str
    date: date
    total: Decimal
    date_is_placeholder: bool = False
    items: list[ReceiptItem] = field(default_factory=list)
    tax: Decimal | None = None
    subtotal: Decimal | None = None
    raw_text: str = ""  # Original OCR text for reference
    image_filename: str = ""  # Source image filename
    warnings: list[ReceiptWarning] = field(default_factory=list)
