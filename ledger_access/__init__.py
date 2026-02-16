"""Centralized ledger access for Beancount files."""

from beanbeaver.ledger_access.reader import get_ledger_reader
from beanbeaver.ledger_access.writer import get_ledger_writer

__all__ = [
    "get_ledger_reader",
    "get_ledger_writer",
]
