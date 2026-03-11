from datetime import date
from decimal import Decimal

from beanbeaver.domain.receipt import Receipt, ReceiptItem, ReceiptWarning
from beanbeaver.receipt.beancount_rendering import (
    format_draft_beancount,
    format_parsed_receipt,
    generate_filename,
)


def test_format_parsed_receipt_renders_expected_metadata_and_warning_anchor() -> None:
    receipt = Receipt(
        merchant='Fresh "Mart"',
        date=date(2026, 3, 5),
        total=Decimal("12.75"),
        tax=Decimal("0.75"),
        raw_text="CARD ********1234\nTOTAL 12.75",
        image_filename="fresh.jpg",
        items=[
            ReceiptItem(
                description='Milk "2%"',
                price=Decimal("4.00"),
                category="Expenses:Food:Grocery:Dairy",
            ),
            ReceiptItem(
                description="Bread",
                price=Decimal("8.00"),
                quantity=2,
                category=None,
            ),
        ],
        warnings=[ReceiptWarning(message="check quantity", after_item_index=1)],
    )

    output = format_parsed_receipt(receipt, image_sha256="abc123")

    assert '; @merchant: Fresh "Mart"' in output
    assert '; @image_sha256: abc123' in output
    assert '2026-03-05 * "Fresh \'Mart\'" "Receipt scan"' in output
    assert "Liabilities:CreditCard:PENDING" in output
    assert "card ****1234" in output
    assert "Expenses:Food:Grocery:Dairy" in output
    assert "; Milk '2%'" in output
    assert "Expenses:FIXME" in output
    assert "; Bread (qty 2)" in output
    assert "; WARN:PARSER check quantity" in output
    assert "; TOTAL 12.75" in output


def test_format_draft_beancount_warns_when_items_exceed_total() -> None:
    receipt = Receipt(
        merchant="Corner Store",
        date=date(2026, 3, 5),
        total=Decimal("5.00"),
        raw_text="TOTAL 5.00",
        items=[
            ReceiptItem(
                description="Item A",
                price=Decimal("3.00"),
                category="Expenses:Food:Grocery:Staple",
            ),
            ReceiptItem(
                description="Item B",
                price=Decimal("3.50"),
                category="Expenses:Food:Grocery:Staple",
            ),
        ],
    )

    output = format_draft_beancount(receipt)

    assert "; === DRAFT - REVIEW NEEDED ===" in output
    assert "  ; WARNING: items total (6.50) exceeds receipt total (5.00)" in output
    assert '; --- Raw OCR Text (for reference) ---' in output


def test_generate_filename_uses_placeholder_and_normalizes_merchant() -> None:
    receipt = Receipt(
        merchant="T&T Super-Market!!",
        date=date(2026, 3, 5),
        total=Decimal("1.00"),
        date_is_placeholder=True,
    )

    assert generate_filename(receipt) == "unknown-date-t-t-super-market.beancount"
