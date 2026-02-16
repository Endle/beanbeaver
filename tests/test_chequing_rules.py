from __future__ import annotations

import pytest

from beanbeaver.runtime.chequing_rules import load_chequing_categorization_patterns
from beanbeaver.domain.chequing_categorization import categorize_chequing_transaction


def test_load_chequing_rules_from_toml(tmp_path) -> None:
    rules_path = tmp_path / "chequing_rules.toml"
    rules_path.write_text(
        """
[[rules]]
pattern = "ACME INC"
account = "Income:Salary"

[[rules]]
pattern = "UTIL BILL"
account = "Expenses:Home:Utility:Electricity"
"""
    )

    load_chequing_categorization_patterns.cache_clear()
    patterns = load_chequing_categorization_patterns(str(rules_path))

    assert patterns == (
        ("ACME INC", "Income:Salary"),
        ("UTIL BILL", "Expenses:Home:Utility:Electricity"),
    )


def test_categorize_transaction_uses_supplied_patterns() -> None:
    patterns = (("PAYROLL", "Income:Salary"), ("INTEREST", "Income:Interest"))
    assert categorize_chequing_transaction("monthly payroll deposit", patterns=patterns) == "Income:Salary"


def test_load_chequing_rules_raises_when_missing(tmp_path) -> None:
    load_chequing_categorization_patterns.cache_clear()
    missing_path = tmp_path / "does_not_exist.toml"
    with pytest.raises(FileNotFoundError):
        load_chequing_categorization_patterns(str(missing_path))


def test_load_chequing_rules_raises_when_empty(tmp_path) -> None:
    rules_path = tmp_path / "chequing_rules.toml"
    rules_path.write_text("")
    load_chequing_categorization_patterns.cache_clear()
    with pytest.raises(ValueError):
        load_chequing_categorization_patterns(str(rules_path))
