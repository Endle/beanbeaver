"""Helpers for discovering accounts from a Beancount ledger."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from beanbeaver.application.imports.shared import select_interactive_option
from beanbeaver.ledger_access import get_ledger_reader

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


def find_open_accounts(
    patterns: list[str],
    *,
    as_of: dt.date | None = None,
    ledger_path: Path | None = None,
) -> list[str]:
    """Return open account names matching any of the patterns."""
    return get_ledger_reader().open_accounts(
        patterns=patterns,
        as_of=as_of,
        ledger_path=ledger_path,
    )


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

    Returns None when no match is found. Prompts on ambiguity if TTY, otherwise fails.
    """
    desc_upper = description.upper()
    for pattern, account_patterns in CC_PAYMENT_RULES:
        if pattern not in desc_upper:
            continue

        if cache is not None and pattern in cache:
            return cache[pattern]

        matches = find_open_accounts(account_patterns, as_of=as_of, ledger_path=ledger_path)
        if not matches:
            if cache is not None:
                cache[pattern] = None
            return None
        if len(matches) == 1:
            if cache is not None:
                cache[pattern] = matches[0]
            return matches[0]
        context = []
        if txn_date:
            context.append(f"date={txn_date.isoformat()}")
        if amount:
            context.append(f"amount={amount}")
        context_str = f" ({', '.join(context)})" if context else ""

        selected = select_interactive_option(
            matches,
            heading=f"Multiple credit card accounts match payment pattern '{pattern}'{context_str}:",
            prompt="Select account (number): ",
            non_tty_error=(
                f"Multiple credit card accounts match payment pattern '{pattern}'{context_str}. "
                "Run interactively to choose"
            ),
            invalid_choice_error="Invalid account selection",
        )
        if cache is not None:
            cache[pattern] = selected
        return selected

    return None


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

    Returns None when no reliable target match is found.
    """
    desc_upper = description.upper()
    if "TRANSFER TO" not in desc_upper and "TRANSFER FROM" not in desc_upper:
        return None
    if any(token in desc_upper for token in _CC_TRANSFER_HINTS):
        return None

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

    for label in candidate_labels:
        cache_key = f"bank-transfer:{label}:{source_account or ''}"
        if cache is not None and cache_key in cache:
            return cache[cache_key]

        patterns = next((rule_patterns for rule_label, rule_patterns in BANK_TRANSFER_RULES if rule_label == label), [])
        matches = find_open_accounts(patterns, as_of=as_of, ledger_path=ledger_path)
        if source_account is not None:
            matches = [account for account in matches if account != source_account]

        if not matches:
            if cache is not None:
                cache[cache_key] = None
            continue
        if len(matches) == 1:
            if cache is not None:
                cache[cache_key] = matches[0]
            return matches[0]

        normalized_label = label.replace(" ", "").upper()
        narrowed = [
            account
            for account in matches
            if normalized_label in account.replace("-", "").replace("_", "").replace(" ", "").upper()
        ]
        if len(narrowed) == 1:
            if cache is not None:
                cache[cache_key] = narrowed[0]
            return narrowed[0]

        if cache is not None:
            cache[cache_key] = None
        return None

    return None
