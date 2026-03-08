"""Tests for bb match abort/rollback session behavior."""

from __future__ import annotations

from pathlib import Path

from beanbeaver.application.receipts.match import _AppliedMatchUndo, _rollback_applied_matches
from beanbeaver.ledger_access import ReceiptMatchFileSnapshot


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_rollback_restores_statement_and_receipt_when_enriched_was_new(tmp_path: Path) -> None:
    approved = tmp_path / "receipts" / "approved" / "r1.beancount"
    matched = tmp_path / "receipts" / "matched" / "r1.beancount"
    statement = tmp_path / "records" / "carda.beancount"
    enriched = tmp_path / "records" / "_enriched" / "r1-enriched.beancount"

    _write(matched, "RECEIPT-CONTENT\n")
    _write(statement, "CHANGED-STATEMENT\n")
    _write(enriched, "NEW-ENRICHED\n")

    undo = _AppliedMatchUndo(
        approved_receipt_path=approved,
        matched_receipt_path=matched,
        ledger_snapshot=ReceiptMatchFileSnapshot(
            statement_path=statement,
            statement_original="ORIGINAL-STATEMENT\n",
            enriched_path=enriched,
            enriched_existed=False,
            enriched_original=None,
        ),
    )

    reverted, warnings = _rollback_applied_matches([undo])

    assert reverted == 1
    assert warnings == []
    assert statement.read_text() == "ORIGINAL-STATEMENT\n"
    assert not enriched.exists()
    assert approved.read_text() == "RECEIPT-CONTENT\n"
    assert not matched.exists()


def test_rollback_handles_receipt_name_collision_and_restores_old_enriched(tmp_path: Path) -> None:
    approved = tmp_path / "receipts" / "approved" / "r1.beancount"
    matched = tmp_path / "receipts" / "matched" / "r1.beancount"
    statement = tmp_path / "records" / "carda.beancount"
    enriched = tmp_path / "records" / "_enriched" / "r1-enriched.beancount"

    _write(approved, "EXISTING-APPROVED\n")
    _write(matched, "MATCHED-RECEIPT\n")
    _write(statement, "CHANGED-STATEMENT\n")
    _write(enriched, "CHANGED-ENRICHED\n")

    undo = _AppliedMatchUndo(
        approved_receipt_path=approved,
        matched_receipt_path=matched,
        ledger_snapshot=ReceiptMatchFileSnapshot(
            statement_path=statement,
            statement_original="ORIGINAL-STATEMENT\n",
            enriched_path=enriched,
            enriched_existed=True,
            enriched_original="ORIGINAL-ENRICHED\n",
        ),
    )

    reverted, warnings = _rollback_applied_matches([undo])

    assert reverted == 1
    assert warnings == []
    assert statement.read_text() == "ORIGINAL-STATEMENT\n"
    assert enriched.read_text() == "ORIGINAL-ENRICHED\n"
    assert approved.read_text() == "EXISTING-APPROVED\n"
    restored = approved.with_name("r1_1.beancount")
    assert restored.read_text() == "MATCHED-RECEIPT\n"
    assert not matched.exists()
