"""Core domain models for the beancount project.

This module provides the core data models used throughout the project:
- CardTransaction: Credit card transaction model
- Receipt, ReceiptItem, Tender: Receipt scanning models

Usage:
    from beanbeaver.domain import CardTransaction, Receipt, ReceiptItem, Tender
"""

from beanbeaver.domain.card_transaction import CardTransaction, create_simple_posting
from beanbeaver.domain.receipt import Receipt, ReceiptItem, Tender, TenderKind

__all__ = [
    "CardTransaction",
    "create_simple_posting",
    "Receipt",
    "ReceiptItem",
    "Tender",
    "TenderKind",
]
