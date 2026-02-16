"""Pure helpers for chequing CSV parsing and rendering."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Iterable


def format_transaction(
    date: dt.date,
    description: str,
    amount: Decimal,
    account: str,
    expense_account: str,
    currency: str = "CAD",
) -> str:
    """Format one transaction as Beancount text."""
    description = description.replace('"', '\\"')

    lines = [
        f'{date.strftime("%Y-%m-%d")} * "{description}" ""',
        f"  {account}  {amount} {currency}",
        f"  {expense_account}  {-amount} {currency}",
        "",
    ]
    return "\n".join(lines)


def format_balance(
    date: dt.date,
    account: str,
    balance: Decimal,
    currency: str = "CAD",
) -> str:
    """Format one balance directive as Beancount text."""
    return f'{date.strftime("%Y-%m-%d")} balance {account}  {balance} {currency}\n'


def build_result_file(
    start_date: str | None = None,
    end_date: str | None = None,
    chequing_type: str | None = None,
) -> str:
    """Build the standard chequing import result filename."""
    prefix = f"{chequing_type}_chequing"
    return f"{prefix}_{start_date}_{end_date}.beancount"


def latest_date(rows: Iterable[tuple[dt.date, str, Decimal, Decimal]]) -> dt.date | None:
    """Return latest date from parsed rows."""
    dates = [row[0] for row in rows]
    return max(dates) if dates else None


def parse_eqbank_rows(
    rows: list[dict[str, str]],
) -> list[tuple[dt.date, str, Decimal, Decimal]]:
    """Parse EQ Bank CSV rows into typed tuples."""
    parsed: list[tuple[dt.date, str, Decimal, Decimal]] = []
    for row in rows:
        date = dt.datetime.strptime(row["Transfer date"], "%Y-%m-%d").date()
        description = row["Description"]
        amount_str = row["Amount"].replace("$", "").replace(",", "")
        balance_str = row["Balance"].replace("$", "").replace(",", "")
        amount_val = Decimal(amount_str)
        balance_val = Decimal(balance_str)
        parsed.append((date, description, amount_val, balance_val))
    return parsed


def parse_scotia_rows(
    rows: list[dict[str, str]],
) -> list[tuple[dt.date, str, Decimal, Decimal]]:
    """Parse Scotia CSV rows into typed tuples."""
    parsed: list[tuple[dt.date, str, Decimal, Decimal]] = []
    for row in rows:
        if not row.get("Date"):
            continue
        date = dt.datetime.strptime(row["Date"], "%Y-%m-%d").date()
        description = row["Description"].strip()
        sub_description = row.get("Sub-description", "").strip()
        if sub_description:
            description = f"{description} - {sub_description}"
        amount_str = row["Amount"].replace("$", "").replace(",", "")
        balance_str = row["Balance"].replace("$", "").replace(",", "")
        amount_val = Decimal(amount_str)
        balance_val = Decimal(balance_str)
        parsed.append((date, description, amount_val, balance_val))
    return parsed
