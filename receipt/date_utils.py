"""Date helpers for receipt parsing and formatting."""

from datetime import date


def placeholder_receipt_date() -> date:
    """Return a valid placeholder date for unknown receipt dates."""
    today = date.today()
    return today.replace(day=1)
