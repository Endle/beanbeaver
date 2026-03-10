"""Public, DTO-oriented API for privileged ledger operations."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from beanbeaver.ledger_access._native import _native_backend
from beanbeaver.ledger_access._paths import default_main_beancount_path

DEFAULT_MAIN_BEANCOUNT_PATH = default_main_beancount_path()


@dataclass(frozen=True)
class LedgerAmount:
    number: Decimal
    currency: str


@dataclass(frozen=True)
class LedgerPosting:
    account: str
    units: LedgerAmount | None


@dataclass(frozen=True)
class LedgerTransaction:
    date: dt.date
    payee: str | None
    narration: str | None
    postings: Sequence[LedgerPosting]
    file_path: str
    line_number: int


@dataclass(frozen=True)
class LedgerTransactionList:
    path: Path
    transactions: list[LedgerTransaction]
    errors: list[str]
    options: dict[str, object]


@dataclass(frozen=True)
class ReceiptMatchSnapshot:
    statement_path: Path
    statement_original: str
    enriched_path: Path
    enriched_existed: bool
    enriched_original: str | None


ReceiptMatchFileSnapshot = ReceiptMatchSnapshot


def _resolve_path(ledger_path: Path | str | None) -> Path:
    return DEFAULT_MAIN_BEANCOUNT_PATH if ledger_path is None else Path(ledger_path)


def list_transactions(*, ledger_path: Path | str | None = None) -> LedgerTransactionList:
    path = _resolve_path(ledger_path)
    raw_path, transactions_payload, errors, options = _native_backend.ledger_access_list_transactions(str(path))
    transactions = [
        LedgerTransaction(
            date=dt.date.fromordinal(int(txn["date_ordinal"])),
            payee=txn["payee"],
            narration=txn["narration"],
            postings=tuple(
                LedgerPosting(
                    account=str(posting["account"]),
                    units=(
                        LedgerAmount(number=Decimal(str(posting["number_str"])), currency=str(posting["currency"]))
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
    return LedgerTransactionList(Path(raw_path), transactions, list(errors), {str(k): v for k, v in dict(options).items()})


def open_accounts(patterns: list[str], *, as_of: dt.date | None = None, ledger_path: Path | str | None = None) -> list[str]:
    if not patterns:
        return []
    as_of = as_of or dt.date.today()
    path = _resolve_path(ledger_path)
    return list(_native_backend.ledger_access_open_accounts(str(path), patterns, as_of.toordinal()))


def transaction_dates_for_account(account: str, *, ledger_path: Path | str | None = None) -> set[dt.date]:
    path = _resolve_path(ledger_path)
    ordinals = _native_backend.ledger_access_transaction_dates_for_account(str(path), account)
    return {dt.date.fromordinal(ordinal) for ordinal in ordinals}


def validate_ledger(*, ledger_path: Path | str | None = None) -> list[str]:
    path = _resolve_path(ledger_path)
    return list(_native_backend.ledger_access_validate_ledger(str(path)))


def apply_receipt_match(*, ledger_path: Path | str | None, statement_path: Path, line_number: int, include_rel_path: str, receipt_name: str, enriched_path: Path, enriched_content: str) -> str:
    path = _resolve_path(ledger_path)
    return str(
        _native_backend.ledger_access_apply_receipt_match(
            str(path),
            str(statement_path),
            line_number,
            include_rel_path,
            receipt_name,
            str(enriched_path),
            enriched_content,
        )
    )


def snapshot_receipt_match_files(*, statement_path: Path, enriched_path: Path) -> ReceiptMatchFileSnapshot:
    statement_original, enriched_existed, enriched_original = _native_backend.ledger_access_snapshot_receipt_match_files(
        str(statement_path),
        str(enriched_path),
    )
    return ReceiptMatchSnapshot(statement_path, statement_original, enriched_path, bool(enriched_existed), enriched_original)


def restore_receipt_match_files(snapshot: ReceiptMatchFileSnapshot) -> None:
    _native_backend.ledger_access_restore_receipt_match_files(
        str(snapshot.statement_path),
        snapshot.statement_original,
        str(snapshot.enriched_path),
        snapshot.enriched_existed,
        snapshot.enriched_original,
    )
