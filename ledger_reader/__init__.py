"""Centralized ledger access for Beancount files."""

from beanbeaver.ledger_reader.reader import get_ledger_reader
from beanbeaver.ledger_reader.writer import get_ledger_writer

__all__ = [
    "get_ledger_reader",
    "get_ledger_writer",
]
