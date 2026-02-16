"""American Express Canada credit card importer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import BaseCardImporter

if TYPE_CHECKING:
    from beancount.ingest.cache import _FileMemo


class AmexImporter(BaseCardImporter):
    """American Express Canada credit cards.

    Caller provides an explicit account selected by the orchestrator layer.
    """

    date_format = "%d %b %Y"
    use_pandas = True

    def __init__(
        self,
        account: str,
        currency: str | None = None,
    ) -> None:
        """Initialize AMEX importer.

        Args:
            account: Beancount account.
            currency: Currency code. Defaults to CAD.
        """
        super().__init__(account=account, currency=currency)

    def identify(self, f: _FileMemo) -> bool:
        return True

    def get_date(self, row: tuple[int, Any]) -> str:
        return row[1]["Date"]

    def get_amount(self, row: tuple[int, Any]) -> str:
        return row[1]["Amount"]

    def get_merchant(self, row: tuple[int, Any]) -> str:
        return row[1]["Description"]
