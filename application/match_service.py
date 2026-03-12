"""Thin Python wrappers over the native match service."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from beanbeaver.ledger_access._native import _native_backend


@dataclass(frozen=True)
class MatchCandidate:
    file_path: str
    line_number: int
    confidence: float
    display: str
    payee: str | None
    narration: str | None
    date: dt.date
    amount: Decimal | None
    details: str
    strength: str


@dataclass(frozen=True)
class ReceiptMatchPlan:
    path: Path
    ledger_path: Path
    candidates: list[MatchCandidate]
    errors: list[str]
    warning: str | None = None
    used_relaxed_threshold: bool = False


@dataclass(frozen=True)
class ApplyMatchResult:
    status: str
    ledger_path: Path
    matched_receipt_path: Path | None = None
    enriched_path: Path | None = None
    message: str | None = None


def _candidate_from_payload(payload: dict[str, object]) -> MatchCandidate:
    amount = payload.get("amount")
    return MatchCandidate(
        file_path=str(payload["file_path"]),
        line_number=int(payload["line_number"]),
        confidence=float(payload["confidence"]),
        display=str(payload["display"]),
        payee=None if payload.get("payee") is None else str(payload["payee"]),
        narration=None if payload.get("narration") is None else str(payload["narration"]),
        date=dt.date.fromisoformat(str(payload["date"])),
        amount=None if amount is None else Decimal(str(amount)),
        details=str(payload.get("details", "")),
        strength=str(payload.get("strength", "strict")),
    )


def _plan_from_payload(payload: dict[str, object]) -> ReceiptMatchPlan:
    return ReceiptMatchPlan(
        path=Path(str(payload["path"])),
        ledger_path=Path(str(payload["ledger_path"])),
        candidates=[_candidate_from_payload(candidate) for candidate in payload["candidates"]],
        errors=[str(error) for error in payload["errors"]],
        warning=None if payload.get("warning") is None else str(payload["warning"]),
        used_relaxed_threshold=bool(payload.get("used_relaxed_threshold", False)),
    )


def plan_receipt_match(
    approved_receipt_path: Path,
    *,
    ledger_path: Path | None = None,
) -> ReceiptMatchPlan:
    payload = _native_backend.match_service_plan_receipt(
        str(approved_receipt_path),
        None if ledger_path is None else str(ledger_path),
    )
    return _plan_from_payload(dict(payload))


def plan_receipt_matches(
    approved_receipt_paths: Sequence[Path],
    *,
    ledger_path: Path | None = None,
) -> list[ReceiptMatchPlan]:
    payloads = _native_backend.match_service_plan_receipts(
        [str(path) for path in approved_receipt_paths],
        None if ledger_path is None else str(ledger_path),
    )
    return [_plan_from_payload(dict(payload)) for payload in payloads]


def apply_receipt_match(
    approved_receipt_path: Path,
    *,
    candidate_file_path: str,
    candidate_line_number: int,
    ledger_path: Path | None = None,
) -> ApplyMatchResult:
    payload = _native_backend.match_service_apply_match(
        str(approved_receipt_path),
        candidate_file_path,
        candidate_line_number,
        None if ledger_path is None else str(ledger_path),
    )
    data = dict(payload)
    matched_receipt_path = data.get("matched_receipt_path")
    enriched_path = data.get("enriched_path")
    return ApplyMatchResult(
        status=str(data["status"]),
        ledger_path=Path(str(data["ledger_path"])),
        matched_receipt_path=None if matched_receipt_path is None else Path(str(matched_receipt_path)),
        enriched_path=None if enriched_path is None else Path(str(enriched_path)),
        message=None if data.get("message") is None else str(data["message"]),
    )
