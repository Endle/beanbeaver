"""Compatibility wrappers over the public ledger_access API."""

from __future__ import annotations

from pathlib import Path

from beanbeaver.ledger_access.api import (
    DEFAULT_MAIN_BEANCOUNT_PATH,
    ReceiptMatchSnapshot,
    apply_receipt_match,
    restore_receipt_match_files,
    snapshot_receipt_match_files,
    validate_ledger,
)



class LedgerWriter:
    def __init__(self, default_ledger_path: Path | None = None) -> None:
        self.default_ledger_path = default_ledger_path or DEFAULT_MAIN_BEANCOUNT_PATH

    def _resolve_path(self, ledger_path: Path | str | None) -> Path:
        return self.default_ledger_path if ledger_path is None else Path(ledger_path)

    def validate_ledger(self, ledger_path: Path | str | None = None) -> list[str]:
        return validate_ledger(ledger_path=self._resolve_path(ledger_path))

    def snapshot_receipt_match_files(self, *, statement_path: Path, enriched_path: Path) -> ReceiptMatchSnapshot:
        return snapshot_receipt_match_files(statement_path=statement_path, enriched_path=enriched_path)

    def restore_receipt_match_files(self, snapshot: ReceiptMatchSnapshot) -> None:
        restore_receipt_match_files(snapshot)

    def _replace_transaction_with_include(self, statement_path: Path, line_number: int, include_rel_path: str, receipt_name: str) -> str:
        from beanbeaver.ledger_access._native import _native_backend

        return str(
            _native_backend.ledger_access_replace_transaction_with_include(
                str(statement_path),
                line_number,
                include_rel_path,
                receipt_name,
            )
        )

    def apply_receipt_match(self, *, ledger_path: Path | str | None, statement_path: Path, line_number: int, include_rel_path: str, receipt_name: str, enriched_path: Path, enriched_content: str) -> str:
        return apply_receipt_match(
            ledger_path=self._resolve_path(ledger_path),
            statement_path=statement_path,
            line_number=line_number,
            include_rel_path=include_rel_path,
            receipt_name=receipt_name,
            enriched_path=enriched_path,
            enriched_content=enriched_content,
        )


_writer: LedgerWriter | None = None


def get_ledger_writer() -> LedgerWriter:
    global _writer
    if _writer is None:
        _writer = LedgerWriter()
    return _writer
