"""Tests for bb match preflight helpers."""

from __future__ import annotations

from beanbeaver.application.receipts.match import _format_ledger_errors


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
