"""Legacy utility exports kept for backward compatibility.

Prefer importing from ``beanbeaver.domain.chequing_categorization``.
"""

from .common_util import (
    categorize_chequing_transaction,
    categorize_transaction,
)

__all__ = [
    "categorize_chequing_transaction",
    "categorize_transaction",
]
