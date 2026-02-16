"""Tests for ledger_reader.reader public behavior."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from beanbeaver.ledger_access.reader import LedgerReader


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_open_accounts_handles_reopened_accounts(tmp_path: Path) -> None:
    ledger = tmp_path / "main.beancount"
    _write(
        ledger,
        """
2020-01-01 open Liabilities:CreditCard:CardA CAD
2021-01-01 close Liabilities:CreditCard:CardA
2022-01-01 open Liabilities:CreditCard:CardA CAD
2020-01-01 open Liabilities:CreditCard:CardB CAD
2021-01-01 close Liabilities:CreditCard:CardB
""".lstrip(),
    )

    reader = LedgerReader(default_ledger_path=ledger)
    open_accounts = reader.open_accounts(
        patterns=["Liabilities:CreditCard:*"],
        as_of=date(2023, 1, 1),
    )

    assert open_accounts == ["Liabilities:CreditCard:CardA"]


def test_open_credit_card_accounts_uses_scoped_prefix(tmp_path: Path) -> None:
    ledger = tmp_path / "main.beancount"
    _write(
        ledger,
        """
2020-01-01 open Liabilities:CreditCard:CardA CAD
2020-01-01 open Liabilities:CreditCardRewards:Promo CAD
""".lstrip(),
    )

    reader = LedgerReader(default_ledger_path=ledger)
    accounts = reader.open_credit_card_accounts(as_of=date(2023, 1, 1))

    assert accounts == ["Liabilities:CreditCard:CardA"]
