"""Helpers for discovering accounts from a Beancount ledger."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from beanbeaver.application.imports.shared import select_interactive_option
from beanbeaver.ledger_access import open_accounts
from beanbeaver.runtime import get_paths

CC_PAYMENT_RULES: list[tuple[str, list[str]]] = [
    (
        "BMO MASTERCARD",
        ["Liabilities:CreditCard:BMO*", "Liabilities:CreditCard:*:BMO:*", "Liabilities:CreditCard:*BMO*"],
    ),
    ("MBNA CANADA MASTERCARD", ["Liabilities:CreditCard:MBNA*"]),
    ("CIBC MASTERCARD", ["Liabilities:CreditCard:CIBC*"]),
    ("SCOTIA VISA", ["Liabilities:CreditCard:Scotia*"]),
    ("CTFS", ["Liabilities:CreditCard:CTFS*"]),
    ("CDN TIRE", ["Liabilities:CreditCard:CTFS*"]),
    ("ROGERS", ["Liabilities:CreditCard:Rogers*"]),
    ("AMEX BILL PYMT", ["Liabilities:CreditCard:Amex*", "Liabilities:CreditCard:AmericanExpress*"]),
]

BANK_TRANSFER_RULES: list[tuple[str, list[str]]] = [
    ("CIBC", ["Assets:Bank:*CIBC*"]),
    ("SCOTIABANK", ["Assets:Bank:*Scotia*"]),
    ("SCOTIA", ["Assets:Bank:*Scotia*"]),
    ("EQ BANK", ["Assets:Bank:*EQBank*"]),
    ("EQBANK", ["Assets:Bank:*EQBank*"]),
    ("TANGERINE", ["Assets:Bank:*Tangerine*"]),
    ("BMO", ["Assets:Bank:*BMO*"]),
    ("HSBC", ["Assets:Bank:*HSBC*"]),
    ("MANULIFE", ["Assets:Bank:*Manulife*"]),
]

_CC_TRANSFER_HINTS = ("MASTERCARD", "VISA", "AMEX", "CREDIT CARD")


ResolutionKind = Literal["resolved", "ambiguous", "no_match"]


@dataclass(frozen=True)
class AccountResolution:
    """Outcome of an account-discovery attempt expressed as data."""

    kind: ResolutionKind
    pattern: str | None = None
    account: str | None = None
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransactionKey:
    """Stable identifier for a parsed transaction row used to key overrides."""

    date: str
    description: str
    amount: str

    @classmethod
    def from_parsed(cls, date: dt.date, description: str, amount: object) -> TransactionKey:
        return cls(date=date.isoformat(), description=description, amount=str(amount))


def find_open_accounts(
    patterns: list[str],
    *,
    as_of: dt.date | None = None,
    ledger_path: Path | None = None,
) -> list[str]:
    """Return open account names matching any of the patterns."""
    resolved_ledger_path = ledger_path if ledger_path is not None else get_paths().main_beancount
    return open_accounts(
        patterns=patterns,
        as_of=as_of,
        ledger_path=resolved_ledger_path,
    )


def resolve_cc_payment_account_strict(
    description: str,
    *,
    as_of: dt.date | None = None,
    ledger_path: Path | None = None,
    cache: dict[str, AccountResolution] | None = None,
) -> AccountResolution:
    """Resolve a CC-payment description without prompting; ambiguity is returned as data."""
    desc_upper = description.upper()
    for pattern, account_patterns in CC_PAYMENT_RULES:
        if pattern not in desc_upper:
            continue

        if cache is not None and pattern in cache:
            return cache[pattern]

        matches = find_open_accounts(account_patterns, as_of=as_of, ledger_path=ledger_path)
        if not matches:
            resolution = AccountResolution(kind="no_match", pattern=pattern)
        elif len(matches) == 1:
            resolution = AccountResolution(kind="resolved", pattern=pattern, account=matches[0])
        else:
            resolution = AccountResolution(
                kind="ambiguous",
                pattern=pattern,
                candidates=tuple(matches),
            )

        if cache is not None:
            cache[pattern] = resolution
        return resolution

    return AccountResolution(kind="no_match")


def resolve_bank_transfer_account_strict(
    description: str,
    *,
    as_of: dt.date | None = None,
    ledger_path: Path | None = None,
    source_account: str | None = None,
    cache: dict[str, AccountResolution] | None = None,
) -> AccountResolution:
    """Resolve a bank-transfer description without prompting; ambiguity is returned as data."""
    desc_upper = description.upper()
    if "TRANSFER TO" not in desc_upper and "TRANSFER FROM" not in desc_upper:
        return AccountResolution(kind="no_match")
    if any(token in desc_upper for token in _CC_TRANSFER_HINTS):
        return AccountResolution(kind="no_match")

    if "TRANSFER TO" in desc_upper:
        target_segment = desc_upper.split("TRANSFER TO", 1)[1]
    elif "TRANSFER FROM" in desc_upper:
        target_segment = desc_upper.split("TRANSFER FROM", 1)[1]
    else:
        target_segment = desc_upper

    seen_labels: set[str] = set()
    candidate_labels: list[str] = []
    for label, _patterns in BANK_TRANSFER_RULES:
        if label in target_segment and label not in seen_labels:
            seen_labels.add(label)
            candidate_labels.append(label)
    for label, _patterns in BANK_TRANSFER_RULES:
        if label in desc_upper and label not in seen_labels:
            seen_labels.add(label)
            candidate_labels.append(label)

    last_seen: AccountResolution = AccountResolution(kind="no_match")
    for label in candidate_labels:
        cache_key = f"bank-transfer:{label}:{source_account or ''}"
        if cache is not None and cache_key in cache:
            cached = cache[cache_key]
            if cached.kind != "no_match":
                return cached
            last_seen = cached
            continue

        patterns = next((rule_patterns for rule_label, rule_patterns in BANK_TRANSFER_RULES if rule_label == label), [])
        matches = find_open_accounts(patterns, as_of=as_of, ledger_path=ledger_path)
        if source_account is not None:
            matches = [account for account in matches if account != source_account]

        if not matches:
            last_seen = AccountResolution(kind="no_match", pattern=label)
            if cache is not None:
                cache[cache_key] = last_seen
            continue
        if len(matches) == 1:
            resolution = AccountResolution(kind="resolved", pattern=label, account=matches[0])
            if cache is not None:
                cache[cache_key] = resolution
            return resolution

        normalized_label = label.replace(" ", "").upper()
        narrowed = [
            account
            for account in matches
            if normalized_label in account.replace("-", "").replace("_", "").replace(" ", "").upper()
        ]
        if len(narrowed) == 1:
            resolution = AccountResolution(kind="resolved", pattern=label, account=narrowed[0])
            if cache is not None:
                cache[cache_key] = resolution
            return resolution

        resolution = AccountResolution(
            kind="ambiguous",
            pattern=label,
            candidates=tuple(matches),
        )
        if cache is not None:
            cache[cache_key] = resolution
        return resolution

    return last_seen


def resolve_cc_payment_account(
    description: str,
    *,
    as_of: dt.date | None = None,
    ledger_path: Path | None = None,
    cache: dict[str, str | None] | None = None,
    txn_date: dt.date | None = None,
    amount: str | None = None,
) -> str | None:
    """
    Resolve a credit card payment description to an open liability account.

    Returns None when no match is found. Prompts on ambiguity if TTY, otherwise raises RuntimeError.
    Kept as the CLI-facing wrapper around resolve_cc_payment_account_strict.
    """
    if cache is not None and description.upper() in cache:
        # Legacy cache contract: keyed by pattern, but callers don't depend on that distinction
        # because each pattern is unique within a description scan. We keep a passthrough wrapper.
        pass

    resolution = resolve_cc_payment_account_strict(
        description,
        as_of=as_of,
        ledger_path=ledger_path,
    )
    if resolution.kind == "no_match":
        if cache is not None and resolution.pattern is not None:
            cache[resolution.pattern] = None
        return None
    if resolution.kind == "resolved":
        if cache is not None and resolution.pattern is not None:
            cache[resolution.pattern] = resolution.account
        return resolution.account

    assert resolution.kind == "ambiguous"
    assert resolution.pattern is not None
    if cache is not None and resolution.pattern in cache:
        return cache[resolution.pattern]

    context = []
    if txn_date:
        context.append(f"date={txn_date.isoformat()}")
    if amount:
        context.append(f"amount={amount}")
    context_str = f" ({', '.join(context)})" if context else ""

    selected = select_interactive_option(
        list(resolution.candidates),
        heading=f"Multiple credit card accounts match payment pattern '{resolution.pattern}'{context_str}:",
        prompt="Select account (number): ",
        non_tty_error=(
            f"Multiple credit card accounts match payment pattern '{resolution.pattern}'{context_str}. "
            "Run interactively to choose"
        ),
        invalid_choice_error="Invalid account selection",
    )
    if cache is not None:
        cache[resolution.pattern] = selected
    return selected


def resolve_bank_transfer_account(
    description: str,
    *,
    as_of: dt.date | None = None,
    ledger_path: Path | None = None,
    source_account: str | None = None,
    cache: dict[str, str | None] | None = None,
) -> str | None:
    """
    Resolve an internal bank transfer description to an open bank account.

    Returns None when no reliable target match is found (preserves prior behavior:
    ambiguity falls through to the categorizer instead of prompting).
    """
    resolution = resolve_bank_transfer_account_strict(
        description,
        as_of=as_of,
        ledger_path=ledger_path,
        source_account=source_account,
    )
    if resolution.kind == "resolved":
        if cache is not None and resolution.pattern is not None:
            cache[f"bank-transfer:{resolution.pattern}:{source_account or ''}"] = resolution.account
        return resolution.account
    return None
