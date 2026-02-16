"""Scotiabank credit card importer."""

from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING, Any

from beanbeaver.runtime import get_logger

from .base import BaseCardImporter

if TYPE_CHECKING:
    from beancount.ingest.cache import _FileMemo

logger = get_logger(__name__)


class ScotiaImporter(BaseCardImporter):
    """Scotiabank credit cards.

    Example:
        importer = ScotiaImporter(account="Liabilities:CreditCard:Scotia:CardA")
    """

    date_format = "%Y-%m-%d"
    use_pandas = True
    skip_rows = 1

    def identify(self, f: _FileMemo) -> bool:
        return True

    def read_rows(self, f: _FileMemo) -> Generator[tuple[int, Any], None, None]:
        import pandas as pd

        df = pd.read_csv(f.name, skiprows=[0], dtype=str, header=None)
        yield from df.iterrows()

    def get_date(self, row: tuple[int, Any]) -> str:
        return row[1][1]

    def get_amount(self, row: tuple[int, Any]) -> str:
        return row[1][6]

    def get_merchant(self, row: tuple[int, Any]) -> str:
        return row[1][2]

    def should_skip(self, row: tuple[int, Any]) -> bool:
        if row[1][5] != "Debit":
            # TODO(security): Raw statement rows may include sensitive transaction data.
            # Keep only for localhost-only operation; redact before non-localhost deployment.
            logger.debug("Skipping bank payment: %s", row[1])
            return True
        return False
