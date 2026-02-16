"""Pure helpers for credit-card import orchestration."""

from __future__ import annotations


def build_result_file(card_name: str, start_date: str | None = None, end_date: str | None = None) -> str:
    """Build the standard credit-card import result filename."""
    fname = card_name.replace("Liabilities:CreditCard:", "")
    fname = fname.replace(":", "_")
    result_file_name = f"{fname}_{start_date}_{end_date}.beancount"
    return result_file_name.lower()
