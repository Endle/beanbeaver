"""Privileged ledger mutation helpers for Beancount files."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from beanbeaver.ledger_access._native import _native_backend
from beanbeaver.ledger_access._paths import default_main_beancount_path

logger = logging.getLogger(f"beancount_local.{__name__}")
DEFAULT_MAIN_BEANCOUNT_PATH = default_main_beancount_path()


@dataclass(frozen=True)
class ReceiptMatchSnapshot:
    """Snapshot of ledger-side files needed to rollback a receipt match."""

    statement_path: Path
    statement_original: str
    enriched_path: Path
    enriched_existed: bool
    enriched_original: str | None


class LedgerWriter:
    """Privileged write access for controlled ledger mutations."""

    def __init__(self, default_ledger_path: Path | None = None) -> None:
        self.default_ledger_path = default_ledger_path or DEFAULT_MAIN_BEANCOUNT_PATH

    def _resolve_path(self, ledger_path: Path | str | None) -> Path:
        if ledger_path is None:
            return self.default_ledger_path
        return Path(ledger_path)

    def validate_ledger(self, ledger_path: Path | str | None = None) -> list[str]:
        """Run Beancount loader validation and return errors (if any)."""
        path = self._resolve_path(ledger_path)
        errors = list(_native_backend.ledger_access_validate_ledger(str(path)))
        if errors:
            logger.warning("Beancount validation found %d error(s) in %s", len(errors), path)
        return errors

    def snapshot_receipt_match_files(
        self,
        *,
        statement_path: Path,
        enriched_path: Path,
    ) -> ReceiptMatchSnapshot:
        """Capture the ledger-side files that a receipt match may modify."""
        statement_original, enriched_existed, enriched_original = (
            _native_backend.ledger_access_snapshot_receipt_match_files(
                str(statement_path),
                str(enriched_path),
            )
        )
        return ReceiptMatchSnapshot(
            statement_path=statement_path,
            statement_original=statement_original,
            enriched_path=enriched_path,
            enriched_existed=bool(enriched_existed),
            enriched_original=enriched_original,
        )

    def restore_receipt_match_files(self, snapshot: ReceiptMatchSnapshot) -> None:
        """Restore ledger-side files from a previously captured snapshot."""
        _native_backend.ledger_access_restore_receipt_match_files(
            str(snapshot.statement_path),
            snapshot.statement_original,
            str(snapshot.enriched_path),
            snapshot.enriched_existed,
            snapshot.enriched_original,
        )

    def _replace_transaction_with_include(
        self,
        statement_path: Path,
        line_number: int,
        include_rel_path: str,
        receipt_name: str,
    ) -> str:
        """
        Replace one transaction with a commented block + include directive.

        Returns:
            "applied" if statement was updated,
            "already_applied" if include already exists.
        """
        return str(
            _native_backend.ledger_access_replace_transaction_with_include(
                str(statement_path),
                line_number,
                include_rel_path,
                receipt_name,
            )
        )

    def apply_receipt_match(
        self,
        *,
        ledger_path: Path | str | None,
        statement_path: Path,
        line_number: int,
        include_rel_path: str,
        receipt_name: str,
        enriched_path: Path,
        enriched_content: str,
    ) -> str:
        """
        Atomically apply receipt enrichment and transaction include replacement.

        On any failure, restores modified files to their original state.
        """
        resolved_ledger_path = self._resolve_path(ledger_path)
        return str(
            _native_backend.ledger_access_apply_receipt_match(
                str(resolved_ledger_path),
                str(statement_path),
                line_number,
                include_rel_path,
                receipt_name,
                str(enriched_path),
                enriched_content,
            )
        )


_writer: LedgerWriter | None = None


def get_ledger_writer() -> LedgerWriter:
    """Return a singleton ledger writer instance."""
    global _writer
    if _writer is None:
        _writer = LedgerWriter()
    return _writer
