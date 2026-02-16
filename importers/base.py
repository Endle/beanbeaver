"""Base class for credit card CSV importers.

This module provides the BaseCardImporter class that all credit card
importers should inherit from. It handles common functionality like
CSV reading, date parsing, and transaction creation.
"""

from __future__ import annotations

import csv
import datetime
import os
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

from beanbeaver.runtime import get_logger
from beancount.core import data
from beancount.ingest import importer

if TYPE_CHECKING:
    from beancount.ingest.cache import _FileMemo

logger = get_logger(__name__)


class BaseCardImporter(importer.ImporterProtocol):
    """Base class for credit card CSV importers.

    Subclasses should define:
        date_format: str - strptime format for dates
        currency: str - currency code (default CAD)

    And implement:
        get_date(row) -> str
        get_amount(row) -> str
        get_merchant(row) -> str

    Optionally override:
        identify(f) - file identification
        should_skip(row) - skip certain rows (payments, etc.)
        determine_account(filename) - dynamic account based on filename
        read_rows(f) - custom CSV reading logic
        _process_row(row, index) - fully custom row processing
        extract(f) - fully custom extraction

    Configuration:
        Caller must provide an explicit account at instantiation:
            importer = CibcImporter(account="Liabilities:CreditCard:CardA")
    """

    # Class-level defaults (subclasses override these)
    date_format: str | None = None
    default_currency: str = "CAD"
    skip_rows: int = 0
    encoding: str = "utf-8"
    use_pandas: bool = False
    identify_filename: str | None = None  # If set, only match this filename

    def __init__(self, account: str, currency: str | None = None) -> None:
        """Initialize the importer with explicit account and optional currency.

        Args:
            account: Beancount account name.
            currency: Currency code. If None, uses class default_currency.
        """
        if not account:
            raise ValueError(f"{self.__class__.__name__} requires a valid account name")
        self.account = account
        self.currency = currency if currency is not None else self.default_currency
        from beanbeaver.runtime import create_rule_engine

        # Importers own categorization so domain models stay rule-engine agnostic.
        self.rule_engine = create_rule_engine(register_python_rules=False)

    def identify(self, f: _FileMemo) -> bool:
        # Routing is centralized in the orchestrator (beanbeaver.application.imports.csv_routing).
        # Keep identify() permissive for ImporterProtocol compatibility only.
        return True

    def determine_account(self, filename: str) -> str:
        """Override to dynamically determine account based on filename."""
        return self.account

    def get_date(self, row: Any) -> str:
        """Extract date string from row. Must be implemented by subclass."""
        raise NotImplementedError

    def get_amount(self, row: Any) -> str:
        """Extract amount string from row. Must be implemented by subclass."""
        raise NotImplementedError

    def get_merchant(self, row: Any) -> str:
        """Extract merchant name from row. Must be implemented by subclass."""
        raise NotImplementedError

    def should_skip(self, row: Any) -> bool:
        """Return True to skip this row (e.g., payments, headers)."""
        return False

    def transform_amount(self, amt: str) -> Any:
        """Transform amount string before creating transaction. Override if needed."""
        return amt

    def read_rows(self, f: _FileMemo) -> Generator[Any, None, None]:
        """Read and yield rows from CSV file. Override for custom parsing."""
        import pandas as pd

        if self.use_pandas:
            df = pd.read_csv(f.name, skiprows=list(range(self.skip_rows)), dtype=str, header="infer")
            for row in df.iterrows():
                yield row
        else:
            with open(f.name, encoding=self.encoding) as file:
                reader = csv.reader(file)
                for _ in range(self.skip_rows):
                    next(reader, None)
                # Skip header
                next(reader, None)
                for row in reader:
                    yield row

    def _process_row(self, row: Any, index: int, account: str) -> data.Transaction | None:
        """Process a single row into a transaction. Override for custom logic."""
        # Import here to avoid circular imports
        from beanbeaver.domain import CardTransaction

        if self.should_skip(row):
            return None

        date_str = self.get_date(row)
        amount_str = self.get_amount(row)
        merchant = self.get_merchant(row)

        amount_val = self.transform_amount(amount_str)

        assert self.date_format is not None
        parsed_date = datetime.datetime.strptime(date_str, self.date_format)
        transaction = CardTransaction(parsed_date, amount_val, merchant, account, "", self.currency)

        meta = data.new_metadata(self.__class__.__name__.lower(), index)
        category = self.rule_engine.categorize(transaction)
        return transaction.create_beancount_transaction(meta=meta, category=category)

    def extract(self, f: _FileMemo) -> list[data.Transaction]:
        """Extract transactions from file. Override for fully custom extraction."""
        entries: list[data.Transaction] = []
        account = self.determine_account(os.path.basename(f.name))

        for index, row in enumerate(self.read_rows(f)):
            txn = self._process_row(row, index, account)
            if txn is not None:
                entries.append(txn)

        return entries
