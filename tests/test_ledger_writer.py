"""Tests for ledger_reader.writer mutation behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from beanbeaver.ledger_reader.writer import LedgerWriter


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_replace_transaction_accepts_non_star_flag(tmp_path: Path) -> None:
    statement = tmp_path / "records" / "carda.beancount"
    _write(
        statement,
        """
2025-01-01 ! "Hold pending" ""
  Expenses:Food:Grocery 10.00 CAD
  Liabilities:CreditCard:CardA -10.00 CAD
""".lstrip(),
    )

    writer = LedgerWriter(default_ledger_path=tmp_path / "main.beancount")
    status = writer._replace_transaction_with_include(
        statement_path=statement,
        line_number=1,
        include_rel_path="_enriched/r1.beancount",
        receipt_name="r1.beancount",
    )

    updated = statement.read_text()
    assert status == "applied"
    assert 'include "_enriched/r1.beancount"' in updated
    assert "; bb-match replaced from receipt r1.beancount on " in updated


def test_replace_transaction_ignores_include_text_in_comments(tmp_path: Path) -> None:
    statement = tmp_path / "records" / "carda.beancount"
    _write(
        statement,
        """
; include "_enriched/r1.beancount"
2025-01-01 * "Groceries" ""
  Expenses:Food:Grocery 10.00 CAD
  Liabilities:CreditCard:CardA -10.00 CAD
""".lstrip(),
    )

    writer = LedgerWriter(default_ledger_path=tmp_path / "main.beancount")
    status = writer._replace_transaction_with_include(
        statement_path=statement,
        line_number=2,
        include_rel_path="_enriched/r1.beancount",
        receipt_name="r1.beancount",
    )

    assert status == "applied"
    assert statement.read_text().count('include "_enriched/r1.beancount"') == 2


def test_apply_receipt_match_preserves_existing_enriched_when_already_applied(tmp_path: Path) -> None:
    ledger = tmp_path / "main.beancount"
    statement = tmp_path / "records" / "carda.beancount"
    enriched = tmp_path / "records" / "_enriched" / "r1.beancount"
    _write(
        ledger,
        """
option "operating_currency" "CAD"
""".lstrip(),
    )
    _write(
        statement,
        """
include "_enriched/r1.beancount"
2025-01-01 * "Groceries" ""
  Expenses:Food:Grocery 10.00 CAD
  Liabilities:CreditCard:CardA -10.00 CAD
""".lstrip(),
    )
    _write(enriched, "OLD-ENRICHED\n")

    writer = LedgerWriter(default_ledger_path=ledger)
    status = writer.apply_receipt_match(
        ledger_path=ledger,
        statement_path=statement,
        line_number=2,
        include_rel_path="_enriched/r1.beancount",
        receipt_name="r1.beancount",
        enriched_path=enriched,
        enriched_content="NEW-ENRICHED\n",
    )

    assert status == "already_applied"
    assert enriched.read_text() == "OLD-ENRICHED\n"


def test_apply_receipt_match_rolls_back_on_validation_failure(tmp_path: Path) -> None:
    statement = tmp_path / "records" / "carda.beancount"
    invalid_ledger = tmp_path / "main.beancount"
    enriched = tmp_path / "records" / "_enriched" / "r1.beancount"
    original_statement = """
2025-01-01 * "Groceries" ""
  Expenses:Food:Grocery 10.00 CAD
  Liabilities:CreditCard:CardA -10.00 CAD
""".lstrip()
    _write(statement, original_statement)
    _write(invalid_ledger, "this is not valid beancount\n")
    enriched.parent.mkdir(parents=True, exist_ok=True)

    writer = LedgerWriter(default_ledger_path=invalid_ledger)
    with pytest.raises(RuntimeError):
        writer.apply_receipt_match(
            ledger_path=invalid_ledger,
            statement_path=statement,
            line_number=1,
            include_rel_path="_enriched/r1.beancount",
            receipt_name="r1.beancount",
            enriched_path=enriched,
            enriched_content="ENRICHED\n",
        )

    assert statement.read_text() == original_statement
    assert not enriched.exists()
