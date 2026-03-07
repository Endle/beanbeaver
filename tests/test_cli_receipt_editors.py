"""Tests for cross-platform editor resolution in receipt commands."""

from __future__ import annotations

from _pytest.monkeypatch import MonkeyPatch
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
