"""Tests for built-in public merchant categorization rules."""

from __future__ import annotations

from pathlib import Path

from beanbeaver.runtime.rule_engine import RuleEngine


class _Txn:
    def __init__(self, raw_merchant_name: str) -> None:
        self.raw_merchant_name = raw_merchant_name


def test_public_struc_tube_rule_applies_without_project_config(tmp_path: Path) -> None:
    engine = RuleEngine(config_path=tmp_path / "missing.toml")
    assert engine.categorize(_Txn("STRUC-TUBE LTD/12424 LAVAL QC")) == "Expenses:Home:Furniture"


def test_project_rule_overrides_public_fallback_rule(tmp_path: Path) -> None:
    config_path = tmp_path / "merchant_rules.toml"
    config_path.write_text(
        """
[[rules]]
keywords = ["STRUC-TUBE"]
category = "Expenses:ProjectSpecific:Override"
""".strip()
    )

    engine = RuleEngine(config_path=config_path)
    assert engine.categorize(_Txn("STRUC-TUBE LTD/12424 LAVAL QC")) == "Expenses:ProjectSpecific:Override"
