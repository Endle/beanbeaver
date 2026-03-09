"""Public, DTO-oriented API for privileged ledger operations."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from beanbeaver.ledger_access.reader import get_ledger_reader
from beanbeaver.ledger_access.writer import ReceiptMatchSnapshot, get_ledger_writer


@dataclass(frozen=True)
class LedgerAmount:
    """Simple amount DTO detached from Beancount value types."""

    number: Decimal
    currency: str


@dataclass(frozen=True)
class LedgerPosting:
    """Simple posting DTO detached from Beancount posting types."""

    account: str
    units: LedgerAmount | None


@dataclass(frozen=True)
class LedgerTransaction:
    """Simple transaction DTO detached from Beancount transaction types."""

    date: dt.date
    payee: str | None
    narration: str | None
    postings: Sequence[LedgerPosting]
    file_path: str
    line_number: int


@dataclass(frozen=True)
class LedgerTransactionList:
    """Transactions loaded from ledger plus loader diagnostics."""

    path: Path
    transactions: list[LedgerTransaction]
    errors: list[str]
    options: dict[str, object]


ReceiptMatchFileSnapshot = ReceiptMatchSnapshot


def list_transactions(
    *,
    ledger_path: Path | str | None = None,
) -> LedgerTransactionList:
    """Return all ledger transactions using DTOs, without Beancount objects."""
    reader = get_ledger_reader()
    path, transactions_payload, errors, options = reader.list_transactions_payload(ledger_path=ledger_path)
    transactions = [
        LedgerTransaction(
            date=dt.date.fromordinal(int(txn["date_ordinal"])),
            payee=txn["payee"],
            narration=txn["narration"],
            postings=tuple(
                LedgerPosting(
                    account=str(posting["account"]),
                    units=(
                        LedgerAmount(
                            number=Decimal(str(posting["number_str"])),
                            currency=str(posting["currency"]),
                        )
                        if posting["number_str"] is not None and posting["currency"] is not None
                        else None
                    ),
                )
                for posting in txn["postings"]
            ),
            file_path=str(txn["file_path"]),
            line_number=int(txn["line_number"]),
        )
        for txn in transactions_payload
    ]
    return LedgerTransactionList(
        path=path,
        transactions=transactions,
        errors=list(errors),
        options={str(k): v for k, v in options.items()},
    )


def open_accounts(
    patterns: list[str],
    *,
    as_of: dt.date | None = None,
    ledger_path: Path | str | None = None,
) -> list[str]:
    """Return open account names matching fnmatch patterns."""
    return get_ledger_reader().open_accounts(
        patterns=patterns,
        as_of=as_of,
        ledger_path=ledger_path,
    )


def transaction_dates_for_account(
    account: str,
    *,
    ledger_path: Path | str | None = None,
) -> set[dt.date]:
    """Return transaction dates where the given account appears."""
    return get_ledger_reader().transaction_dates_for_account(account, ledger_path=ledger_path)


def validate_ledger(
    *,
    ledger_path: Path | str | None = None,
) -> list[str]:
    """Validate ledger and return diagnostics as strings."""
    return [str(err) for err in get_ledger_writer().validate_ledger(ledger_path=ledger_path)]


def apply_receipt_match(
    *,
    ledger_path: Path | str | None,
    statement_path: Path,
    line_number: int,
    include_rel_path: str,
    receipt_name: str,
    enriched_path: Path,
    enriched_content: str,
) -> str:
    """Apply a receipt match transaction replacement."""
    return get_ledger_writer().apply_receipt_match(
        ledger_path=ledger_path,
        statement_path=statement_path,
        line_number=line_number,
        include_rel_path=include_rel_path,
        receipt_name=receipt_name,
        enriched_path=enriched_path,
        enriched_content=enriched_content,
    )


def snapshot_receipt_match_files(
    *,
    statement_path: Path,
    enriched_path: Path,
) -> ReceiptMatchFileSnapshot:
    """Capture ledger-side file state so a later rollback can restore it."""
    return get_ledger_writer().snapshot_receipt_match_files(
        statement_path=statement_path,
        enriched_path=enriched_path,
    )


def restore_receipt_match_files(snapshot: ReceiptMatchFileSnapshot) -> None:
    """Restore ledger-side file state captured before applying a receipt match."""
    get_ledger_writer().restore_receipt_match_files(snapshot)
