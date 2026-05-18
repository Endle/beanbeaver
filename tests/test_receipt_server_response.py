"""Contract tests for /upload response payload builders in receipt_server."""

from datetime import date
from decimal import Decimal

from beanbeaver.domain.receipt import Receipt, ReceiptItem, ReceiptWarning
from beanbeaver.runtime.receipt_server import (
    build_upload_error_payload,
    build_upload_success_payload,
)


def _receipt_basic() -> Receipt:
    return Receipt(
        merchant="Loblaws",
        date=date(2026, 5, 16),
        total=Decimal("32.70"),
        subtotal=Decimal("29.10"),
        tax=Decimal("3.60"),
        items=[
            ReceiptItem(description="Milk", price=Decimal("4.50")),
            ReceiptItem(description="Bread", price=Decimal("3.25")),
        ],
    )


def test_success_payload_contains_parsed_block_and_summary() -> None:
    receipt = _receipt_basic()

    payload = build_upload_success_payload(
        receipt,
        draft_filename="review_stage_1.receipt.json",
        image_filename="receipt_20260516_120000.jpg",
        image_sha256="deadbeef",
        size_bytes=12345,
    )

    assert payload["status"] == "success"
    assert payload["action"] == "saved_for_review"
    assert payload["draft_filename"] == "review_stage_1.receipt.json"
    assert payload["image_sha256"] == "deadbeef"
    assert payload["size_bytes"] == 12345

    parsed = payload["parsed"]
    assert parsed["merchant"] == "Loblaws"
    assert parsed["date"] == "2026-05-16"
    assert parsed["date_is_placeholder"] is False
    assert parsed["total"] == "32.70"
    assert parsed["subtotal"] == "29.10"
    assert parsed["tax"] == "3.60"
    assert parsed["item_count"] == 2
    assert parsed["warnings"] == []

    assert payload["summary"] == "Loblaws · 2026-05-16 · $32.70 · 2 items"


def test_success_payload_handles_placeholder_date_and_missing_totals() -> None:
    receipt = Receipt(
        merchant="UNKNOWN",
        date=date(1970, 1, 1),
        date_is_placeholder=True,
        total=Decimal("9.00"),
        items=[ReceiptItem(description="Candy", price=Decimal("9.00"))],
        warnings=[ReceiptWarning(message="low confidence on date")],
    )

    payload = build_upload_success_payload(
        receipt,
        draft_filename="x.json",
        image_filename="x.jpg",
        image_sha256="00",
        size_bytes=1,
    )

    parsed = payload["parsed"]
    assert parsed["date_is_placeholder"] is True
    assert parsed["subtotal"] is None
    assert parsed["tax"] is None
    assert parsed["warnings"] == ["low confidence on date"]
    assert payload["summary"] == "UNKNOWN · UNKNOWN · $9.00 · 1 item"


def test_total_serialises_as_two_decimal_string_not_float() -> None:
    receipt = _receipt_basic()
    receipt.total = Decimal("7.5")
    payload = build_upload_success_payload(
        receipt,
        draft_filename="x.json",
        image_filename="x.jpg",
        image_sha256="00",
        size_bytes=1,
    )
    assert payload["parsed"]["total"] == "7.50"
    assert "$7.50" in payload["summary"]


def test_error_payload_carries_error_code_and_summary() -> None:
    payload = build_upload_error_payload(
        error_code="ocr_unreachable",
        message="OCR service unreachable at http://localhost:8001",
    )
    assert payload == {
        "status": "error",
        "error_code": "ocr_unreachable",
        "message": "OCR service unreachable at http://localhost:8001",
        "summary": "OCR service unreachable at http://localhost:8001",
    }
