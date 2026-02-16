"""Backward-compatible shim for chequing categorization helpers.

Prefer importing from ``beanbeaver.domain.chequing_categorization``.
"""

from __future__ import annotations

from beanbeaver.domain.chequing_categorization import (
    categorize_chequing_transaction,
    categorize_transaction,
)

__all__ = [
    "categorize_chequing_transaction",
    "categorize_transaction",
]
