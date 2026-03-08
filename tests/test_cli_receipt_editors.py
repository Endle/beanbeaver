"""Tests for cross-platform editor resolution in receipt commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from _pytest.capture import CaptureFixture
from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.application.receipts.review import ReEditApprovedReceiptResult
from beanbeaver.cli import receipt as receipt_cli


def test_resolve_editor_prefers_visual_over_editor(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(receipt_cli.shutil, "which", lambda cmd: None)
    monkeypatch.setenv("VISUAL", "code --wait")
    monkeypatch.setenv("EDITOR", "nano")

    assert receipt_cli._resolve_editor() == ["code", "--wait"]


def test_default_editor_uses_notepad_on_windows(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(receipt_cli.os, "name", "nt")
    monkeypatch.setattr(receipt_cli.shutil, "which", lambda cmd: "C:\\Windows\\notepad.exe" if cmd == "notepad" else None)

    assert receipt_cli._default_editor_command() == ["notepad"]


def test_default_editor_prefers_nano_on_posix(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(receipt_cli.os, "name", "posix")
    monkeypatch.setattr(receipt_cli.shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if cmd == "nano" else None)

    assert receipt_cli._default_editor_command() == ["nano"]


def test_cmd_re_edit_accepts_direct_path(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    target = tmp_path / "receipts" / "json" / "approved" / "r1" / "parsed.receipt.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    updated = target.parent / "review_stage_1.receipt.json"
    captured_path: dict[str, Path] = {}

    class _TtyStdin:
        def isatty(self) -> bool:
            return True

    def _fake_run(request: object) -> ReEditApprovedReceiptResult:
        captured_path["target"] = request.target_path
        return ReEditApprovedReceiptResult(status="updated", updated_path=updated)

    monkeypatch.setattr(receipt_cli.sys, "stdin", _TtyStdin())
    monkeypatch.setattr(
        "beanbeaver.application.receipts.review.run_re_edit_approved_receipt",
        _fake_run,
    )

    receipt_cli.cmd_re_edit(argparse.Namespace(path=str(target)))

    assert captured_path["target"] == target.resolve()
    assert f"Updated approved receipt: {updated}" in capsys.readouterr().out
