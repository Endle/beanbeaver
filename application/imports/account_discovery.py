"""Helpers for discovering accounts from a Beancount ledger."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from beanbeaver.ledger_reader import get_ledger_reader

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

        if not sys.stdin.isatty():
            raise RuntimeError(
                "Multiple credit card accounts match payment pattern "
                f"'{pattern}'{context_str}. Run interactively to choose: {', '.join(matches)}"
            )
        print(f"Multiple credit card accounts match payment pattern '{pattern}'{context_str}:")
        for idx, account in enumerate(matches, 1):
            print(f"  {idx}. {account}")
        choice = input("Select account (number): ").strip()
        try:
            selected = matches[int(choice) - 1]
        except (ValueError, IndexError):
            raise RuntimeError("Invalid account selection") from None
        if cache is not None:
            cache[pattern] = selected
        return selected

    return None
