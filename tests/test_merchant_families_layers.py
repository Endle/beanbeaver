"""Tests for layered merchant-family rule loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.runtime import merchant_families as merchant_families_module


@dataclass
class _Paths:
    merchant_families: Path
    default_merchant_families: Path


def test_load_merchant_families_merges_project_and_public_defaults(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    project_families = tmp_path / "merchant_families.toml"
    public_families = tmp_path / "default_merchant_families.toml"
    project_families.write_text(
        """
[[families]]
canonical = "PRIVATE STORE"
aliases = ["PRIVATE ALT"]
""".strip(),
        encoding="utf-8",
    )
    public_families.write_text(
        """
[[families]]
canonical = "PUBLIC STORE"
aliases = ["PUBLIC ALT"]
""".strip(),
        encoding="utf-8",
    )

    merchant_families_module.load_merchant_families.cache_clear()
    monkeypatch.setattr(
        merchant_families_module,
        "get_paths",
        lambda: _Paths(
            merchant_families=project_families,
            default_merchant_families=public_families,
        ),
    )

    families = merchant_families_module.load_merchant_families()

    assert [(family.canonical, family.aliases) for family in families] == [
        ("PRIVATE STORE", ("PRIVATE ALT",)),
        ("PUBLIC STORE", ("PUBLIC ALT",)),
    ]


def test_load_merchant_families_with_explicit_path_uses_only_that_file(tmp_path: Path) -> None:
    explicit_families = tmp_path / "merchant_families.toml"
    explicit_families.write_text(
        """
[[families]]
canonical = "EXPLICIT STORE"
aliases = ["EXPLICIT ALT"]
""".strip(),
        encoding="utf-8",
    )

    merchant_families_module.load_merchant_families.cache_clear()
    families = merchant_families_module.load_merchant_families(str(explicit_families))

    assert [(family.canonical, family.aliases) for family in families] == [
        ("EXPLICIT STORE", ("EXPLICIT ALT",)),
    ]
