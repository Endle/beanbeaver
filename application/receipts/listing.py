"""Receipt listing workflow orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from beanbeaver.runtime.receipt_storage import list_approved_receipts, list_scanned_receipts


@dataclass(frozen=True)
class ApprovedReceiptListing:
    """Approved receipt summaries for CLI display."""

    receipts: list[tuple[Path, str, date, Decimal]]


@dataclass(frozen=True)
class ScannedReceiptListing:
    """Scanned receipt file list for CLI display."""

    receipts: list[Path]


def run_list_approved_receipts() -> ApprovedReceiptListing:
    """Load approved receipt summaries."""
    return ApprovedReceiptListing(receipts=list_approved_receipts())


def run_list_scanned_receipts() -> ScannedReceiptListing:
    """Load scanned receipt paths."""
    return ScannedReceiptListing(receipts=list_scanned_receipts())

