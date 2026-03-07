"""Tests for bank-transfer account resolution in chequing imports."""

from __future__ import annotations

from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.application.imports import account_discovery


def test_resolve_bank_transfer_account_matches_unique_target(monkeypatch: MonkeyPatch) -> None:
    def fake_find_open_accounts(*args: object, **kwargs: object) -> list[str]:
        return ["Assets:Bank:Chequing:CIBC1234"]

    monkeypatch.setattr(account_discovery, "find_open_accounts", fake_find_open_accounts)

    resolved = account_discovery.resolve_bank_transfer_account("Transfer to CIBC Chequing")
    assert resolved == "Assets:Bank:Chequing:CIBC1234"


def test_resolve_bank_transfer_account_excludes_source_account(monkeypatch: MonkeyPatch) -> None:
    def fake_find_open_accounts(*args: object, **kwargs: object) -> list[str]:
        return [
            "Assets:Bank:Chequing:EQBankJoint0914",
            "Assets:Bank:Chequing:EQBankEmergency",
        ]

    monkeypatch.setattr(account_discovery, "find_open_accounts", fake_find_open_accounts)

    resolved = account_discovery.resolve_bank_transfer_account(
        "Transfer to EQ Bank",
        source_account="Assets:Bank:Chequing:EQBankJoint0914",
    )
    assert resolved == "Assets:Bank:Chequing:EQBankEmergency"


def test_resolve_bank_transfer_account_returns_none_when_ambiguous(monkeypatch: MonkeyPatch) -> None:
    def fake_find_open_accounts(*args: object, **kwargs: object) -> list[str]:
        return [
            "Assets:Bank:Chequing:BMO:Joint",
            "Assets:Bank:Chequing:BMO:Personal",
        ]

    monkeypatch.setattr(account_discovery, "find_open_accounts", fake_find_open_accounts)

    resolved = account_discovery.resolve_bank_transfer_account("Transfer to BMO Chequing")
    assert resolved is None


def test_resolve_bank_transfer_account_ignores_credit_card_transfers(monkeypatch: MonkeyPatch) -> None:
    called = {"count": 0}

    def fake_find_open_accounts(*args: object, **kwargs: object) -> list[str]:
        called["count"] += 1
        return ["Assets:Bank:Chequing:CIBC1234"]

    monkeypatch.setattr(account_discovery, "find_open_accounts", fake_find_open_accounts)

    resolved = account_discovery.resolve_bank_transfer_account("Transfer to CIBC Mastercard")
    assert resolved is None
    assert called["count"] == 0
