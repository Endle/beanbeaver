"""Tests for ambiguous-account decisions surfaced via preflight + override-driven apply."""

from __future__ import annotations

import datetime as dt
import importlib
from decimal import Decimal
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.application.imports import account_discovery, chequing


def _install_open_accounts(
    monkeypatch: MonkeyPatch,
    *,
    cc_accounts: list[str],
    chequing_accounts: list[str] | None = None,
    bank_transfer_accounts: list[str] | None = None,
) -> None:
    if bank_transfer_accounts is None:
        bank_transfer_accounts = []
    if chequing_accounts is None:
        chequing_accounts = ["Assets:Bank:Chequing:EQBankJoint0914"]

    def fake_find_open_accounts(patterns: list[str], **_kwargs: object) -> list[str]:
        joined = " ".join(patterns)
        if "Liabilities:CreditCard" in joined:
            return cc_accounts
        if "Assets:Bank:Chequing" in joined:
            return chequing_accounts
        if "Assets:Bank" in joined:
            return bank_transfer_accounts
        return []

    monkeypatch.setattr(account_discovery, "find_open_accounts", fake_find_open_accounts)
    monkeypatch.setattr(chequing, "find_open_accounts", fake_find_open_accounts)


def test_strict_cc_resolver_returns_ambiguous_data(monkeypatch: MonkeyPatch) -> None:
    _install_open_accounts(
        monkeypatch,
        cc_accounts=[
            "Liabilities:CreditCard:Amex:Gold",
            "Liabilities:CreditCard:Amex:Plat",
        ],
    )
    resolution = account_discovery.resolve_cc_payment_account_strict(
        "AMEX BILL PYMT 04APR",
    )
    assert resolution.kind == "ambiguous"
    assert resolution.pattern == "AMEX BILL PYMT"
    assert resolution.candidates == (
        "Liabilities:CreditCard:Amex:Gold",
        "Liabilities:CreditCard:Amex:Plat",
    )


def test_strict_cc_resolver_returns_resolved_when_unique(monkeypatch: MonkeyPatch) -> None:
    _install_open_accounts(monkeypatch, cc_accounts=["Liabilities:CreditCard:Amex:Plat"])
    resolution = account_discovery.resolve_cc_payment_account_strict("AMEX BILL PYMT")
    assert resolution.kind == "resolved"
    assert resolution.account == "Liabilities:CreditCard:Amex:Plat"


def test_strict_cc_resolver_returns_no_match_when_pattern_missing(monkeypatch: MonkeyPatch) -> None:
    _install_open_accounts(monkeypatch, cc_accounts=[])
    resolution = account_discovery.resolve_cc_payment_account_strict("Coffee Shop")
    assert resolution.kind == "no_match"


def test_legacy_cc_wrapper_raises_when_ambiguous_non_tty(monkeypatch: MonkeyPatch) -> None:
    _install_open_accounts(
        monkeypatch,
        cc_accounts=[
            "Liabilities:CreditCard:Amex:Gold",
            "Liabilities:CreditCard:Amex:Plat",
        ],
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    try:
        account_discovery.resolve_cc_payment_account(
            "AMEX BILL PYMT",
            txn_date=dt.date(2026, 4, 6),
            amount="-883.35 CAD",
        )
    except RuntimeError as exc:
        assert "AMEX BILL PYMT" in str(exc)
    else:
        raise AssertionError("expected RuntimeError on ambiguous CC resolution without TTY")


def _write_eqbank_csv(path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    """Write a minimal EQ Bank-format CSV with given (date, description, amount, balance) tuples."""
    header = "Transfer date,Description,Amount,Balance\n"
    body = "\n".join(",".join(cell for cell in row) for row in rows)
    path.write_text(header + body + "\n", encoding="utf-8")


def test_preflight_emits_per_row_decisions_for_repeated_pattern(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "Eqbank.csv"
    _write_eqbank_csv(
        csv_path,
        rows=[
            ("2026-04-06", "AMEX BILL PYMT 04APR", "-883.35", "1000.00"),
            ("2026-04-08", "AMEX BILL PYMT 08APR", "-150.00", "850.00"),
        ],
    )

    _install_open_accounts(
        monkeypatch,
        cc_accounts=[
            "Liabilities:CreditCard:Amex:Gold",
            "Liabilities:CreditCard:Amex:Plat",
        ],
        chequing_accounts=["Assets:Bank:Chequing:EQBankJoint0914"],
    )

    result = chequing.preflight_chequing_import(csv_file=str(csv_path))
    assert result.status == "ok"
    assert result.chequing_type == "eqbank"
    assert result.account == "Assets:Bank:Chequing:EQBankJoint0914"
    assert len(result.decisions) == 2

    by_amount = {decision.transaction.amount: decision for decision in result.decisions}
    assert set(by_amount) == {"-883.35", "-150.00"}
    for decision in result.decisions:
        assert decision.kind == "cc_payment"
        assert decision.pattern == "AMEX BILL PYMT"
        assert set(decision.candidates) == {
            "Liabilities:CreditCard:Amex:Gold",
            "Liabilities:CreditCard:Amex:Plat",
        }


def test_preflight_returns_error_when_csv_missing(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    result = chequing.preflight_chequing_import(csv_file=str(tmp_path / "missing.csv"))
    assert result.status == "error"
    assert result.error is not None


def test_preflight_returns_error_when_chequing_account_unknown(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "Eqbank.csv"
    _write_eqbank_csv(
        csv_path,
        rows=[("2026-04-06", "PAYROLL", "1000.00", "1000.00")],
    )
    _install_open_accounts(
        monkeypatch,
        cc_accounts=[],
        chequing_accounts=[],
    )
    result = chequing.preflight_chequing_import(csv_file=str(csv_path))
    assert result.status == "error"
    assert result.error is not None


def test_apply_with_overrides_skips_prompts_per_row(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "Eqbank.csv"
    _write_eqbank_csv(
        csv_path,
        rows=[
            ("2026-04-06", "AMEX BILL PYMT 04APR", "-883.35", "1000.00"),
            ("2026-04-08", "AMEX BILL PYMT 08APR", "-150.00", "850.00"),
        ],
    )

    _install_open_accounts(
        monkeypatch,
        cc_accounts=[
            "Liabilities:CreditCard:Amex:Gold",
            "Liabilities:CreditCard:Amex:Plat",
        ],
        chequing_accounts=["Assets:Bank:Chequing:EQBankJoint0914"],
    )
    monkeypatch.setattr(chequing, "confirm_uncommitted_changes", lambda *_a, **_k: True)
    monkeypatch.setattr(chequing, "get_existing_transaction_dates", lambda _account: set())
    monkeypatch.setattr(chequing, "validate_ledger", lambda **_kwargs: [])
    monkeypatch.setattr(
        chequing,
        "load_chequing_categorization_patterns",
        lambda: (("__NO_MATCH__", "Expenses:Uncategorized"),),
    )

    captured: dict[str, object] = {}

    def fake_write(*, output_content: str, result_file_name: str, **_kwargs: object) -> Path:
        captured["output"] = output_content
        captured["name"] = result_file_name
        return tmp_path / result_file_name

    monkeypatch.setattr(chequing, "write_import_output", fake_write)

    overrides = (
        chequing.TransactionOverride(
            transaction=account_discovery.TransactionKey(
                date="2026-04-06",
                description="AMEX BILL PYMT 04APR",
                amount="-883.35",
            ),
            account="Liabilities:CreditCard:Amex:Gold",
        ),
        chequing.TransactionOverride(
            transaction=account_discovery.TransactionKey(
                date="2026-04-08",
                description="AMEX BILL PYMT 08APR",
                amount="-150.00",
            ),
            account="Liabilities:CreditCard:Amex:Plat",
        ),
    )

    result = chequing.run_chequing_import(
        chequing.ChequingImportRequest(
            csv_file=str(csv_path),
            allow_uncommitted=True,
            cc_payment_overrides=overrides,
            interactive=False,
        ),
        emit_console_output=False,
    )
    assert result.status == "ok", result.error
    output = captured["output"]
    assert isinstance(output, str)
    assert "Liabilities:CreditCard:Amex:Gold" in output
    assert "Liabilities:CreditCard:Amex:Plat" in output


def test_apply_without_overrides_returns_clean_error_for_ambiguous(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "Eqbank.csv"
    _write_eqbank_csv(
        csv_path,
        rows=[
            ("2026-04-06", "AMEX BILL PYMT 04APR", "-883.35", "1000.00"),
        ],
    )
    _install_open_accounts(
        monkeypatch,
        cc_accounts=[
            "Liabilities:CreditCard:Amex:Gold",
            "Liabilities:CreditCard:Amex:Plat",
        ],
    )
    monkeypatch.setattr(chequing, "confirm_uncommitted_changes", lambda *_a, **_k: True)
    monkeypatch.setattr(chequing, "get_existing_transaction_dates", lambda _account: set())
    monkeypatch.setattr(
        chequing,
        "load_chequing_categorization_patterns",
        lambda: (("__NO_MATCH__", "Expenses:Uncategorized"),),
    )

    result = chequing.run_chequing_import(
        chequing.ChequingImportRequest(
            csv_file=str(csv_path),
            allow_uncommitted=True,
            interactive=False,
        ),
        emit_console_output=False,
    )
    assert result.status == "error"
    assert result.error is not None
    assert "ambiguous" in result.error.lower()


def test_decimal_smoke_module_import() -> None:
    """Sanity import: the module is fresh-loadable with no circular issues."""
    importlib.reload(chequing)
    assert hasattr(chequing, "preflight_chequing_import")
    assert isinstance(Decimal("1.00"), Decimal)
