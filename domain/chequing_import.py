"""Pure helpers for chequing CSV parsing and rendering."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from decimal import Decimal

# (date, description, amount, balance). Balance is None for statements that
# carry no running balance column (e.g. Wealthsimple activity exports).
ParsedChequingRow = tuple[dt.date, str, Decimal, Decimal | None]


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
    return f"{date.strftime('%Y-%m-%d')} balance {account}  {balance} {currency}\n"


def build_result_file(
    start_date: str | None = None,
    end_date: str | None = None,
    chequing_type: str | None = None,
) -> str:
    """Build the standard chequing import result filename."""
    prefix = f"{chequing_type}_chequing"
    return f"{prefix}_{start_date}_{end_date}.beancount"


def latest_date(rows: Iterable[ParsedChequingRow]) -> dt.date | None:
    """Return latest date from parsed rows."""
    dates = [row[0] for row in rows]
    return max(dates) if dates else None


def next_day(value: dt.date) -> dt.date:
    """Return the next calendar day."""
    return value + dt.timedelta(days=1)


def parse_eqbank_rows(
    rows: list[dict[str, str]],
) -> list[ParsedChequingRow]:
    """Parse EQ Bank CSV rows into typed tuples."""
    parsed: list[ParsedChequingRow] = []
    for row in rows:
        date = dt.datetime.strptime(row["Transfer date"], "%Y-%m-%d").date()
        description = row["Description"]
        amount_str = row["Amount"].replace("$", "").replace(",", "")
        balance_str = row["Balance"].replace("$", "").replace(",", "")
        amount_val = Decimal(amount_str)
        balance_val = Decimal(balance_str)
        parsed.append((date, description, amount_val, balance_val))
    return parsed


# Wealthsimple restates the posting date inside the description; drop the noise.
_WEALTHSIMPLE_EXECUTED_AT_RE = re.compile(r"\s*\(executed at [^)]*\)\s*$")

WEALTHSIMPLE_CHEQUING_ACCOUNT_TYPE = "Chequing"


def clean_wealthsimple_description(description: str) -> str:
    """Strip Wealthsimple's redundant '(executed at ...)' suffix from a description."""
    return _WEALTHSIMPLE_EXECUTED_AT_RE.sub("", description.strip()).strip()


def parse_wealthsimple_rows(
    rows: list[dict[str, str]],
) -> list[ParsedChequingRow]:
    """Parse Wealthsimple activity-export rows into typed tuples.

    The export bundles every Wealthsimple account and ends with an "As of ..."
    footer line, so rows outside the chequing account (and the footer) are
    skipped. Wealthsimple does not publish a running balance, so the balance
    element is always None and no balance directives can be asserted.
    """
    parsed: list[ParsedChequingRow] = []
    for row in rows:
        if (row.get("account_type") or "").strip() != WEALTHSIMPLE_CHEQUING_ACCOUNT_TYPE:
            continue
        try:
            date = dt.datetime.strptime((row.get("transaction_date") or "").strip(), "%Y-%m-%d").date()
        except ValueError:
            continue
        description = clean_wealthsimple_description(row.get("description") or "")
        amount_str = (row.get("net_cash_amount") or "").replace("$", "").replace(",", "").strip()
        if not amount_str:
            continue
        parsed.append((date, description, Decimal(amount_str), None))
    return parsed


def parse_scotia_rows(
    rows: list[dict[str, str]],
) -> list[ParsedChequingRow]:
    """Parse Scotia CSV rows into typed tuples."""
    parsed: list[ParsedChequingRow] = []
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
