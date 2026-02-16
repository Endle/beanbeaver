"""Credit card importers for Canadian banks.

This package provides importers for various Canadian credit cards and banks.
All importers inherit from BaseCardImporter and can be used with beancount's
import system.

Supported institutions:
- CIBC (including Simplii)
- BMO
- Scotiabank
- Rogers Bank
- MBNA
- PC Financial
- Canadian Tire Financial Services
- American Express Canada

Configuration:
    Card importers require explicit account names from caller:

        from importers import CibcImporter, BmoImporter

        CONFIG = [
            CibcImporter(
                account="Liabilities:CreditCard:CIBC:CardA",
                simplii_account="Liabilities:CreditCard:CIBC:CardB",
            ),
            BmoImporter(account="Liabilities:CreditCard:BMO:CardA"),
        ]

    Some importers (CIBC, AMEX) handle multiple card variants and accept
    additional parameters. See individual importer docstrings for details.
"""

from .amex import AmexImporter
from .base import BaseCardImporter
from .bmo import BmoImporter
from .canadian_tire_financial import CanadianTireFinancialImporter
from .cibc import CibcImporter
from .mbna import MbnaImporter
from .pcf import PcfImporter
from .rogers import RogersImporter
from .scotia import ScotiaImporter

__all__ = [
    "BaseCardImporter",
    "AmexImporter",
    "BmoImporter",
    "CibcImporter",
    "CanadianTireFinancialImporter",
    "MbnaImporter",
    "PcfImporter",
    "RogersImporter",
    "ScotiaImporter",
]

# Caller-owned configuration. Intentionally empty in vendor defaults so
# account names are always provided by project code.
CONFIG: list[BaseCardImporter] = []
