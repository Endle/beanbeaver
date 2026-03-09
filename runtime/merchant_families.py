"""Runtime loader for merchant-family identity rules."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from beanbeaver.runtime.paths import get_paths

_PACKAGE_DEFAULT_MERCHANT_FAMILIES = Path(__file__).resolve().parents[1] / "rules" / "default_merchant_families.toml"


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


def _unique_existing_paths(paths: list[Path | None]) -> list[Path]:
    resolved_seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        if path is None:
            continue
        resolved = path.resolve()
        if resolved in resolved_seen:
            continue
        resolved_seen.add(resolved)
        result.append(path)
    return result


@lru_cache(maxsize=4)
def load_merchant_families(config_path: str | None = None) -> tuple[MerchantFamily, ...]:
    """Load layered merchant-family rules from project-local and public defaults."""
    families: list[MerchantFamily] = []
    if config_path is not None:
        return tuple(_load_families_from_path(Path(config_path)))

    paths = get_paths()
    package_default = None
    if not paths.default_merchant_families.exists():
        package_default = _PACKAGE_DEFAULT_MERCHANT_FAMILIES
    legacy_default = getattr(paths, "legacy_default_merchant_families", None)
    for path in _unique_existing_paths(
        [
            paths.merchant_families,
            paths.default_merchant_families,
            package_default,
            legacy_default if isinstance(legacy_default, Path) else None,
        ]
    ):
        families.extend(_load_families_from_path(path))
    return tuple(families)
