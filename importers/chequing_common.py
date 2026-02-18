"""Shared helpers for chequing importers."""

from __future__ import annotations

import datetime


def next_day(value: datetime.date) -> datetime.date:
    """Return the next calendar day."""
    return value + datetime.timedelta(days=1)
