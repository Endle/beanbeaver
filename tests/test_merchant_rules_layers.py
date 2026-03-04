"""Tests for layered merchant-rule keyword loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.runtime import merchant_rules as merchant_rules_module


@dataclass
class _Paths:
    merchant_rules: Path
    default_merchant_rules: Path


def test_load_known_merchant_keywords_merges_project_and_public_defaults(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    project_rules = tmp_path / "merchant_rules.toml"
    public_rules = tmp_path / "default_merchant_rules.toml"
    project_rules.write_text(
        """
[[rules]]
keywords = ["PRIVATE_ONLY"]
category = "Expenses:Private"
""".strip()
    )
    public_rules.write_text(
        """
[[rules]]
keywords = ["PUBLIC_ONLY"]
category = "Expenses:Public"
""".strip()
    )

    merchant_rules_module.load_known_merchant_keywords.cache_clear()
    monkeypatch.setattr(
        merchant_rules_module,
        "get_paths",
        lambda: _Paths(merchant_rules=project_rules, default_merchant_rules=public_rules),
    )

    keywords = merchant_rules_module.load_known_merchant_keywords()

    assert keywords == ("PRIVATE_ONLY", "PUBLIC_ONLY")


def test_load_known_merchant_keywords_with_explicit_path_uses_only_that_file(tmp_path: Path) -> None:
    explicit_rules = tmp_path / "explicit_rules.toml"
    explicit_rules.write_text(
        """
[[rules]]
keywords = ["ONLY_EXPLICIT"]
category = "Expenses:Explicit"
""".strip()
    )

    merchant_rules_module.load_known_merchant_keywords.cache_clear()
    keywords = merchant_rules_module.load_known_merchant_keywords(str(explicit_rules))

    assert keywords == ("ONLY_EXPLICIT",)
