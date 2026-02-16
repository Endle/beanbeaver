"""Pure helpers for chequing transaction categorization."""

from __future__ import annotations


def categorize_chequing_transaction(
    description: str,
    *,
    patterns: list[tuple[str, str]] | tuple[tuple[str, str], ...],
) -> str | None:
    """Categorize a chequing transaction based on description patterns."""
    if not patterns:
        raise ValueError("Chequing categorization patterns are required and must be non-empty")

    desc_upper = description.upper()
    for pattern, account in patterns:
        if pattern in desc_upper:
            return account
    return None


# Backward-compatible function alias. Prefer the chequing-specific name above.
categorize_transaction = categorize_chequing_transaction
