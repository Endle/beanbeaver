"""Backward-compatible shim for chequing categorization helpers.

Prefer importing from ``beanbeaver.domain.chequing_categorization``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "categorize_chequing_transaction",
    "categorize_transaction",
]


def _load_chequing_module() -> Any:
    # Keep util free of direct beanbeaver imports; resolve implementation lazily.
    return import_module("beanbeaver.domain.chequing_categorization")


def categorize_chequing_transaction(*args: Any, **kwargs: Any) -> str:
    module = _load_chequing_module()
    return module.categorize_chequing_transaction(*args, **kwargs)


def categorize_transaction(*args: Any, **kwargs: Any) -> str:
    module = _load_chequing_module()
    return module.categorize_transaction(*args, **kwargs)
