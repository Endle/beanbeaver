"""Pure helpers for receipt-to-ledger matching flows."""

from __future__ import annotations

from decimal import Decimal


def find_transaction_end(lines: list[str], start_idx: int) -> int:
    """Find the exclusive end index of a Beancount transaction block."""
    idx = start_idx + 1
    while idx < len(lines):
        line = lines[idx]
        if line.strip() == "":
            idx += 1
            break
        if line.startswith((" ", "\t")):
            idx += 1
            continue
        break
    return idx


def comment_block(lines: list[str]) -> list[str]:
    """Comment out each non-empty line while preserving newlines."""
    out: list[str] = []
    for line in lines:
        if line.strip() == "":
            out.append(line)
        elif line.lstrip().startswith(";"):
            out.append(line)
        else:
            out.append(f"; {line}")
    return out


def transaction_charge_amount(match: object) -> Decimal | None:
    """Return absolute CC charge amount from the matched transaction."""
    txn = getattr(match, "transaction", None)
    if txn is None:
        return None
    for posting in txn.postings:
        if posting.units and posting.units.number < 0:
            return abs(posting.units.number)
    return None


def itemized_receipt_total(receipt: object) -> Decimal:
    """Return receipt total represented by itemized lines plus tax."""
    total = sum((item.total for item in receipt.items), Decimal("0"))
    if getattr(receipt, "tax", None):
        total += receipt.tax
    return total


def match_key(match: object) -> tuple[str, int]:
    """Stable key for deduplicating selected transactions within one run."""
    file_path = str(getattr(match, "file_path", "unknown"))
    line_number = int(getattr(match, "line_number", 0))
    return (file_path, line_number)
