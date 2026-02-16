"""Privileged ledger mutation helpers for Beancount files."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from beanbeaver.domain.match import comment_block, find_transaction_end
from beanbeaver.runtime import get_logger, get_paths
from beancount.loader import load_file

logger = get_logger(__name__)
_TXN_START_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\*")


class LedgerWriter:
    """Privileged write access for controlled ledger mutations."""

    def __init__(self, default_ledger_path: Path | None = None) -> None:
        self.default_ledger_path = default_ledger_path or get_paths().main_beancount

    def _resolve_path(self, ledger_path: Path | str | None) -> Path:
        if ledger_path is None:
            return self.default_ledger_path
        return Path(ledger_path)

    def validate_ledger(self, ledger_path: Path | str | None = None) -> list[Any]:
        """Run Beancount loader validation and return errors (if any)."""
        path = self._resolve_path(ledger_path)
        _, errors, _ = load_file(str(path))
        if errors:
            logger.warning("Beancount validation found %d error(s) in %s", len(errors), path)
        return list(errors)

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
        content = statement_path.read_text()
        include_prefix = f'include "{include_rel_path}"'
        if include_prefix in content:
            return "already_applied"

        lines = content.splitlines(keepends=True)
        start_idx = line_number - 1
        if start_idx < 0 or start_idx >= len(lines):
            raise ValueError(f"Invalid line number {line_number} for {statement_path}")
        if not _TXN_START_RE.match(lines[start_idx].lstrip()):
            raise ValueError(
                f"Line {line_number} in {statement_path} is not a transaction start: {lines[start_idx].rstrip()}"
            )

        end_idx = find_transaction_end(lines, start_idx)
        original_block = lines[start_idx:end_idx]
        if not original_block:
            raise ValueError(f"Empty transaction block at {statement_path}:{line_number}")

        stamp = date.today().isoformat()
        replacement: list[str] = [
            f"; bb-match replaced from receipt {receipt_name} on {stamp}\n",
            *comment_block(original_block),
        ]
        if replacement and replacement[-1].strip() != "":
            replacement.append("\n")
        replacement.append(f"{include_prefix}  ; bb-match: {receipt_name}\n")
        replacement.append("\n")

        new_lines = [*lines[:start_idx], *replacement, *lines[end_idx:]]
        statement_path.write_text("".join(new_lines))
        return "applied"

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
        original_statement = statement_path.read_text()
        enriched_existed = enriched_path.exists()
        original_enriched = enriched_path.read_text() if enriched_existed else None

        try:
            enriched_path.write_text(enriched_content)
            status = self._replace_transaction_with_include(
                statement_path=statement_path,
                line_number=line_number,
                include_rel_path=include_rel_path,
                receipt_name=receipt_name,
            )

            apply_errors = self.validate_ledger(ledger_path=ledger_path)
            if apply_errors:
                error_preview = "; ".join(str(err) for err in apply_errors[:2])
                raise RuntimeError(f"ledger validation failed after replacement: {error_preview}")

            return status
        except Exception:
            statement_path.write_text(original_statement)
            if enriched_existed and original_enriched is not None:
                enriched_path.write_text(original_enriched)
            elif enriched_path.exists():
                enriched_path.unlink()
            raise


_writer: LedgerWriter | None = None


def get_ledger_writer() -> LedgerWriter:
    """Return a singleton ledger writer instance."""
    global _writer
    if _writer is None:
        _writer = LedgerWriter()
    return _writer
