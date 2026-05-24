"""Runtime loader for merchant-family identity rules."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from beanbeaver.runtime.paths import get_paths


@dataclass(frozen=True)
class MerchantFamily:
    """One canonical merchant identity and its aliases."""

    canonical: str
    aliases: tuple[str, ...]


def _load_families_from_path(path: Path) -> list[MerchantFamily]:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    if not path.exists():
        return []

    with open(path, "rb") as f:
        config = tomllib.load(f)

    families: list[MerchantFamily] = []
    for family in config.get("families", []):
        canonical = str(family.get("canonical", "")).strip()
        raw_aliases = family.get("aliases", [])
        aliases = tuple(alias.strip() for alias in raw_aliases if isinstance(alias, str) and alias.strip())
        if canonical:
            families.append(MerchantFamily(canonical=canonical, aliases=aliases))
    return families


@lru_cache(maxsize=4)
def load_merchant_families(config_path: str | None = None) -> tuple[MerchantFamily, ...]:
    """Load layered merchant-family rules from project-local and public defaults."""
    if config_path is not None:
        return tuple(_load_families_from_path(Path(config_path)))

    paths = get_paths()
    families: list[MerchantFamily] = []
    for path in (paths.merchant_families, paths.default_merchant_families):
        families.extend(_load_families_from_path(path))
    return tuple(families)
