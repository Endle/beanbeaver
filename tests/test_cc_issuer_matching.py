"""Order-independent issuer matching for credit-card account discovery."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from beanbeaver.application.imports.account_discovery import (
    find_open_cc_accounts_for_issuer,
    resolve_cc_payment_account_strict,
)

_LEDGER = """
option "operating_currency" "CAD"
2023-01-01 open Liabilities:CreditCard:Tama:Rogers:WorldElite CAD
2023-01-01 open Liabilities:CreditCard:Wang:Rogers:WorldElite CAD
2023-01-01 open Liabilities:CreditCard:CIBC:Costco CAD
""".lstrip()


def _ledger(tmp_path: Path) -> Path:
    ledger = tmp_path / "main.beancount"
    ledger.write_text(_LEDGER)
    return ledger


def test_matches_issuer_behind_owner_segment(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    matches = find_open_cc_accounts_for_issuer(["Rogers"], as_of=date(2026, 1, 1), ledger_path=ledger)

    # Anchored "Liabilities:CreditCard:Rogers*" would miss both of these owner-segmented accounts.
    assert matches == [
        "Liabilities:CreditCard:Tama:Rogers:WorldElite",
        "Liabilities:CreditCard:Wang:Rogers:WorldElite",
    ]


def test_issuer_match_stays_scoped_to_other_issuers(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    assert find_open_cc_accounts_for_issuer(["CIBC"], as_of=date(2026, 1, 1), ledger_path=ledger) == [
        "Liabilities:CreditCard:CIBC:Costco",
    ]


def test_cc_payment_resolution_matches_owner_segmented_rogers(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    resolution = resolve_cc_payment_account_strict(
        "ROGERS BANK PAYMENT",
        as_of=date(2026, 1, 1),
        ledger_path=ledger,
    )

    # Two open Rogers cards -> ambiguous (caller prompts), rather than the old no_match.
    assert resolution.kind == "ambiguous"
    assert resolution.candidates == (
        "Liabilities:CreditCard:Tama:Rogers:WorldElite",
        "Liabilities:CreditCard:Wang:Rogers:WorldElite",
    )
