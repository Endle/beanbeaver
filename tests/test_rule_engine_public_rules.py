"""Tests for built-in public merchant categorization rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.runtime import rule_engine as rule_engine_module
from beanbeaver.runtime.rule_engine import RuleEngine


class _Txn:
    def __init__(self, raw_merchant_name: str) -> None:
        self.raw_merchant_name = raw_merchant_name


@dataclass
class _Paths:
    merchant_rules: Path
    default_merchant_rules: Path
    legacy_default_merchant_rules: Path


def test_public_struc_tube_rule_applies_without_project_config(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    public_rules = Path(__file__).resolve().parents[1] / "rules" / "default_merchant_rules.toml"
    monkeypatch.setattr(
        rule_engine_module,
        "get_paths",
        lambda: _Paths(
            merchant_rules=tmp_path / "merchant_rules.toml",
            default_merchant_rules=public_rules,
            legacy_default_merchant_rules=public_rules,
        ),
    )
    engine = RuleEngine(config_path=tmp_path / "missing.toml")
    assert engine.categorize(_Txn("STRUC-TUBE LTD/12424 LAVAL QC")) == "Expenses:Home:Furniture"


def test_public_grocery_and_home_rules_apply_without_project_config(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    public_rules = Path(__file__).resolve().parents[1] / "rules" / "default_merchant_rules.toml"
    monkeypatch.setattr(
        rule_engine_module,
        "get_paths",
        lambda: _Paths(
            merchant_rules=tmp_path / "merchant_rules.toml",
            default_merchant_rules=public_rules,
            legacy_default_merchant_rules=public_rules,
        ),
    )
    engine = RuleEngine(config_path=tmp_path / "missing.toml")

    assert engine.categorize(_Txn("FOODY MART")) == "Expenses:Food:Grocery"
    assert engine.categorize(_Txn("TREDISH GROCERIES TORO")) == "Expenses:Food:Grocery"
    assert engine.categorize(_Txn("ONE S BETTER LIVING")) == "Expenses:Home"
    assert engine.categorize(_Txn("MINISO CANADA")) == "Expenses:Home"


def test_public_grocery_rules_match_statement_merchant_strings(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Merchants seen on statements (with city suffixes / truncation) categorize as grocery."""
    public_rules = Path(__file__).resolve().parents[1] / "rules" / "default_merchant_rules.toml"
    monkeypatch.setattr(
        rule_engine_module,
        "get_paths",
        lambda: _Paths(
            merchant_rules=tmp_path / "merchant_rules.toml",
            default_merchant_rules=public_rules,
            legacy_default_merchant_rules=public_rules,
        ),
    )
    engine = RuleEngine(config_path=tmp_path / "missing.toml")

    assert engine.categorize(_Txn("FOODY MART MARKHAM ON")) == "Expenses:Food:Grocery"
    assert engine.categorize(_Txn("DAVE & ANNETTE'S NO FR MARKHAM ON")) == "Expenses:Food:Grocery"
    assert engine.categorize(_Txn("WAL-MART #3053 MARKHAM ON")) == "Expenses:Food:Grocery"


def test_public_utility_and_takeout_rules_match_statement_merchant_strings(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Enercare home services categorize as utility; Auric King BBQ as takeout."""
    public_rules = Path(__file__).resolve().parents[1] / "rules" / "default_merchant_rules.toml"
    monkeypatch.setattr(
        rule_engine_module,
        "get_paths",
        lambda: _Paths(
            merchant_rules=tmp_path / "merchant_rules.toml",
            default_merchant_rules=public_rules,
            legacy_default_merchant_rules=public_rules,
        ),
    )
    engine = RuleEngine(config_path=tmp_path / "missing.toml")

    assert engine.categorize(_Txn("ENERCARE HOME SERVICES MARKHAM ON")) == "Expenses:Home:Utility"
    assert engine.categorize(_Txn("AURIC KING BBQ TAKE OU MARKHAM ON")) == "Expenses:Food:Restaurant:TakeOut"


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
