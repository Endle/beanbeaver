"""Receipt scan workflow orchestration."""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from beanbeaver.receipt.formatter import format_parsed_receipt
from beanbeaver.receipt.ocr_result_parser import parse_receipt
from beanbeaver.runtime import load_known_merchant_keywords
from beanbeaver.runtime.receipt_pipeline import OCRServiceUnavailable, call_ocr_service, save_ocr_json
from beanbeaver.runtime.receipt_storage import move_scanned_to_approved, save_scanned_receipt

if TYPE_CHECKING:
    from beanbeaver.domain.receipt import Receipt

ScanStatus = Literal[
    "file_not_found",
    "ocr_unavailable",
    "scanned_saved",
    "editor_not_found",
    "editor_failed",
    "approved_staged",
]


@dataclass(frozen=True)
class ReceiptScanRequest:
    """Inputs for running receipt scan workflow."""

    image_path: Path
    ocr_url: str
    no_edit: bool
    resolve_editor_cmd: Callable[[], list[str]] | None = None


@dataclass(frozen=True)
class ReceiptScanResult:
    """Outcome from receipt scan workflow."""

    status: ScanStatus
    receipt: Receipt | None = None
    scanned_path: Path | None = None
    approved_path: Path | None = None
    error: str | None = None
    editor_cmd: list[str] | None = None
    editor_returncode: int | None = None


def run_receipt_scan(request: ReceiptScanRequest) -> ReceiptScanResult:
    """Run scan flow: OCR -> parse -> save scanned -> optional edit -> stage approved."""
    if not request.image_path.exists():
        return ReceiptScanResult(
            status="file_not_found",
            error=f"Receipt file not found: {request.image_path}",
        )

    try:
        raw_ocr_result, ocr_result = call_ocr_service(request.image_path, request.ocr_url)
    except OCRServiceUnavailable as exc:
        return ReceiptScanResult(
            status="ocr_unavailable",
            error=str(exc),
        )

    save_ocr_json(raw_ocr_result, request.image_path)

    receipt = parse_receipt(
        ocr_result,
        image_filename=request.image_path.name,
        known_merchants=load_known_merchant_keywords(),
    )
    image_sha256 = hashlib.sha256(request.image_path.read_bytes()).hexdigest()
    beancount_content = format_parsed_receipt(receipt, image_sha256=image_sha256)
    scanned_path = save_scanned_receipt(receipt, beancount_content)

    if request.no_edit:
        return ReceiptScanResult(
            status="scanned_saved",
            receipt=receipt,
            scanned_path=scanned_path,
        )

    if request.resolve_editor_cmd is None:
        return ReceiptScanResult(
            status="scanned_saved",
            receipt=receipt,
            scanned_path=scanned_path,
        )

    editor_cmd = request.resolve_editor_cmd()
    try:
        result = subprocess.run(editor_cmd + [str(scanned_path)])
    except FileNotFoundError:
        return ReceiptScanResult(
            status="editor_not_found",
            receipt=receipt,
            scanned_path=scanned_path,
            editor_cmd=editor_cmd,
        )

    if result.returncode != 0:
        return ReceiptScanResult(
            status="editor_failed",
            receipt=receipt,
            scanned_path=scanned_path,
            editor_cmd=editor_cmd,
            editor_returncode=result.returncode,
        )

    approved_path = move_scanned_to_approved(scanned_path)
    return ReceiptScanResult(
        status="approved_staged",
        receipt=receipt,
        scanned_path=scanned_path,
        approved_path=approved_path,
        editor_cmd=editor_cmd,
    )
