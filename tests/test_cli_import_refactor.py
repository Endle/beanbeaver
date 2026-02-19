"""Regression tests for typed CLI import handoff and argv isolation."""

from __future__ import annotations

import sys

from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.application.imports import chequing as chequing_import
from beanbeaver.application.imports import credit_card as credit_card_import
from beanbeaver.cli import main as unified_cli


def test_unified_cli_cc_import_handoff_does_not_mutate_sys_argv(monkeypatch: MonkeyPatch) -> None:
    captured_request: credit_card_import.CreditCardImportRequest | None = None

    def fake_run(request: credit_card_import.CreditCardImportRequest) -> credit_card_import.CreditCardImportResult:
        nonlocal captured_request
        captured_request = request
        return credit_card_import.CreditCardImportResult(status="ok")

    sentinel_argv = ["sentinel", "keep-this"]
    monkeypatch.setattr(sys, "argv", sentinel_argv)
    monkeypatch.setattr(credit_card_import, "run_credit_card_import", fake_run)

    exit_code = unified_cli.main(["import", "cc", "statement.csv", "0101", "0131"])

    assert exit_code == 0
    assert sys.argv == sentinel_argv
    assert captured_request == credit_card_import.CreditCardImportRequest(
        csv_file="statement.csv",
        start_date="0101",
        end_date="0131",
    )


def test_unified_cli_chequing_import_handoff_does_not_mutate_sys_argv(monkeypatch: MonkeyPatch) -> None:
    captured_request: chequing_import.ChequingImportRequest | None = None

    def fake_run(request: chequing_import.ChequingImportRequest) -> chequing_import.ChequingImportResult:
        nonlocal captured_request
        captured_request = request
        return chequing_import.ChequingImportResult(status="ok")

    sentinel_argv = ["sentinel", "keep-this"]
    monkeypatch.setattr(sys, "argv", sentinel_argv)
    monkeypatch.setattr(chequing_import, "run_chequing_import", fake_run)

    exit_code = unified_cli.main(["import", "chequing", "Preferred_Package_foo.csv"])

    assert exit_code == 0
    assert sys.argv == sentinel_argv
    assert captured_request == chequing_import.ChequingImportRequest(csv_file="Preferred_Package_foo.csv")


def test_credit_card_main_parses_argv_into_typed_request(monkeypatch: MonkeyPatch) -> None:
    captured_request: credit_card_import.CreditCardImportRequest | None = None

    def fake_run(request: credit_card_import.CreditCardImportRequest) -> credit_card_import.CreditCardImportResult:
        nonlocal captured_request
        captured_request = request
        return credit_card_import.CreditCardImportResult(status="ok")

    monkeypatch.setattr(credit_card_import, "run_credit_card_import", fake_run)

    exit_code = credit_card_import.main(["manual.csv", "0201", "0228"])

    assert exit_code == 0
    assert captured_request == credit_card_import.CreditCardImportRequest(
        csv_file="manual.csv",
        start_date="0201",
        end_date="0228",
    )


def test_chequing_main_parses_argv_into_typed_request(monkeypatch: MonkeyPatch) -> None:
    captured_request: chequing_import.ChequingImportRequest | None = None

    def fake_run(request: chequing_import.ChequingImportRequest) -> chequing_import.ChequingImportResult:
        nonlocal captured_request
        captured_request = request
        return chequing_import.ChequingImportResult(status="ok")

    monkeypatch.setattr(chequing_import, "run_chequing_import", fake_run)

    exit_code = chequing_import.main(["manual.csv"])

    assert exit_code == 0
    assert captured_request == chequing_import.ChequingImportRequest(csv_file="manual.csv")
