"""bean-extract config entrypoint for credit card importers.

This lives in the CLI/orchestrator layer. It provides the CONFIG object that
bean-extract expects when run with a script path.
"""

from __future__ import annotations

import sys
from pathlib import Path

# When bean-extract runs this file via run_path(), the vendor root isn't
# guaranteed to be on sys.path. Add it so `beanbeaver.*` imports resolve.
_VENDOR_ROOT = Path(__file__).resolve().parents[2]
if str(_VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_VENDOR_ROOT))

from beanbeaver.importers import (  # noqa: E402
    AmexImporter,
    BaseCardImporter,
    BmoImporter,
    CanadianTireFinancialImporter,
    CibcImporter,
    MbnaImporter,
    PcfImporter,
    RogersImporter,
    ScotiaImporter,
)

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
    "CONFIG",
]

# Project should provide explicit account names; no vendor hard-coded defaults.
CONFIG: list[BaseCardImporter] = []
