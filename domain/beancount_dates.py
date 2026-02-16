"""Pure helpers for working with Beancount text snippets."""

from __future__ import annotations

import re


def extract_dates_from_beancount(
    content: str,
    include_balance: bool = False,
) -> tuple[str | None, str | None]:
    """
    Extract min and max dates from Beancount text.

    Returns MMDD date strings as (min_date, max_date), or (None, None) when no
    date directive is found.
    """
    if include_balance:
        date_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+[*!balance]", re.MULTILINE)
    else:
        date_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+[*!]", re.MULTILINE)

    dates = date_pattern.findall(content)
    if not dates:
        return None, None

    dates.sort()
    min_date = dates[0].replace("-", "")[4:]  # "2025-01-15" -> "0115"
    max_date = dates[-1].replace("-", "")[4:]
    return min_date, max_date
