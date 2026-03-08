"""Tests for bb match preflight helpers."""

from __future__ import annotations

from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.application.receipts.match import (
    _format_ledger_errors,
    _prompt_failed_match_recovery,
    _re_edit_receipt_after_failed_match,
)
from beanbeaver.application.receipts.review import ReEditApprovedReceiptResult


class _Err:
    def __init__(self, *, source: object = None, message: str | None = None) -> None:
        self.source = source
        self.message = message

    def __str__(self) -> str:
        return self.message or "unknown-error"


def test_format_ledger_errors_includes_filename_and_line() -> None:
    errors = [
        _Err(
            source={"filename": "/tmp/main.beancount", "lineno": 12},
            message="syntax error",
        )
    ]

    assert _format_ledger_errors(errors) == ["/tmp/main.beancount:12 - syntax error"]


def test_format_ledger_errors_limits_output() -> None:
    errors = [_Err(message=f"err-{i}") for i in range(7)]
    assert _format_ledger_errors(errors, limit=3) == ["err-0", "err-1", "err-2"]


def test_prompt_failed_match_recovery_accepts_edit_after_invalid(monkeypatch: MonkeyPatch) -> None:
    responses = iter(["wat", "1"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    assert _prompt_failed_match_recovery() == "edit"


def test_prompt_failed_match_recovery_defaults_to_skip(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "")

    assert _prompt_failed_match_recovery() == "skip"


def test_re_edit_receipt_after_failed_match_returns_updated_path(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "receipts" / "json" / "approved" / "r1" / "parsed.receipt.json"
    updated = target.parent / "review_stage_1.receipt.json"

    def _fake_run(request: object) -> ReEditApprovedReceiptResult:
        return ReEditApprovedReceiptResult(status="updated", updated_path=updated)

    monkeypatch.setattr(
        "beanbeaver.application.receipts.review.run_re_edit_approved_receipt",
        _fake_run,
    )

    assert _re_edit_receipt_after_failed_match(target, resolve_editor_cmd=lambda: ["nano"]) == updated
