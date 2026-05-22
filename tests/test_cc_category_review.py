"""Tests for credit-card category preflight + override-driven apply."""

from __future__ import annotations

import datetime as dt

from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.application.imports import credit_card
from beancount.core import amount, data, flags
from beancount.core.number import D


def _txn(date: str, payee: str, amount_str: str, category: str) -> data.Transaction:
    """Build a CC-shaped transaction: one card posting, one expense posting."""
    expense_amount = amount.Amount(D(amount_str), "CAD")
    card_amount = amount.Amount(-D(amount_str), "CAD")
    return data.Transaction(
        meta={},
        date=dt.date.fromisoformat(date),
        flag=flags.FLAG_OKAY,
        payee=payee,
        narration="",
        tags=frozenset(),
        links=frozenset(),
        postings=[
            data.Posting("Liabilities:CreditCard:Amex:Gold", card_amount, None, None, None, None),
            data.Posting(category, expense_amount, None, None, None, None),
        ],
    )


def _install_prepared(monkeypatch: MonkeyPatch, entries: list[data.Directive], *, as_of: dt.date | None = None) -> None:
    prepared = credit_card._PreparedEntries(
        entries=entries,
        card_account="Liabilities:CreditCard:Amex:Gold",
        as_of=as_of,
    )
    monkeypatch.setattr(
        credit_card,
        "_prepare_card_entries",
        lambda **_kwargs: (None, prepared),
    )


def test_preflight_lists_entries_uncategorized_first(monkeypatch: MonkeyPatch) -> None:
    entries: list[data.Directive] = [
        _txn("2026-05-01", "STARBUCKS", "6.50", "Expenses:Food:Coffee"),
        _txn("2026-05-02", "UNKNOWN SHOP", "42.00", "Expenses:Uncategorized"),
        _txn("2026-05-03", "TINY", "3.00", "Expenses:Shopping:NotAssigned"),
    ]
    _install_prepared(monkeypatch, entries)
    monkeypatch.setattr(
        credit_card,
        "find_open_accounts",
        lambda *_a, **_k: ["Expenses:Food:Coffee", "Expenses:Food:Grocery"],
    )

    result = credit_card.preflight_credit_card_categories(csv_file="amex.csv")

    assert result.status == "ok"
    assert result.card_account == "Liabilities:CreditCard:Amex:Gold"
    # Uncategorized (and NotAssigned) first; rule-matched after.
    assert [e.payee for e in result.entries] == ["UNKNOWN SHOP", "TINY", "STARBUCKS"]
    assert [e.uncategorized for e in result.entries] == [True, True, False]
    # In-use categories are appended to candidates even when not "open".
    assert "Expenses:Uncategorized" in result.candidate_categories
    assert "Expenses:Shopping:NotAssigned" in result.candidate_categories
    assert "Expenses:Food:Grocery" in result.candidate_categories


def test_preflight_propagates_preparation_error(monkeypatch: MonkeyPatch) -> None:
    error = credit_card.CreditCardImportResult(status="error", error="File not found: amex.csv")
    monkeypatch.setattr(credit_card, "_prepare_card_entries", lambda **_kwargs: (error, None))

    result = credit_card.preflight_credit_card_categories(csv_file="amex.csv")

    assert result.status == "error"
    assert result.error == "File not found: amex.csv"
    assert result.entries == ()


def test_apply_category_overrides_rewrites_matching_expense_posting() -> None:
    entries: list[data.Directive] = [
        _txn("2026-05-02", "UNKNOWN SHOP", "42.00", "Expenses:Uncategorized"),
        _txn("2026-05-01", "STARBUCKS", "6.50", "Expenses:Food:Coffee"),
    ]
    overrides = (
        credit_card.CategoryOverride(
            date="2026-05-02",
            payee="UNKNOWN SHOP",
            amount="42.00",
            category="Expenses:Shopping:Electronics",
        ),
    )

    credit_card._apply_category_overrides(entries, overrides)

    assert entries[0].postings[1].account == "Expenses:Shopping:Electronics"
    # Untouched transaction keeps its rule-assigned category.
    assert entries[1].postings[1].account == "Expenses:Food:Coffee"


def test_apply_category_overrides_ignores_non_matching_keys() -> None:
    entries: list[data.Directive] = [_txn("2026-05-02", "UNKNOWN SHOP", "42.00", "Expenses:Uncategorized")]
    overrides = (
        credit_card.CategoryOverride(
            date="2026-05-02",
            payee="DIFFERENT PAYEE",
            amount="42.00",
            category="Expenses:Shopping:Electronics",
        ),
    )

    credit_card._apply_category_overrides(entries, overrides)

    assert entries[0].postings[1].account == "Expenses:Uncategorized"
