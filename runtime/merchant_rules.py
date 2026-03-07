"""Runtime loader for merchant categorization rules."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from beanbeaver.runtime.paths import get_paths


def _load_keywords_from_path(path: Path) -> list[str]:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    if not path.exists():
        return []

    with open(path, "rb") as f:
        config = tomllib.load(f)

    keywords: list[str] = []
    for rule in config.get("rules", []):
        keywords.extend(rule.get("keywords", []))
    return keywords


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
def load_known_merchant_keywords(config_path: str | None = None) -> tuple[str, ...]:
    """
    Load known merchant keywords from runtime merchant-rule layers.

    Args:
        config_path: Optional TOML path override. If None, merges
            project-local and vendor default merchant rules.

    Returns:
        Tuple of merchant keywords from all rules, preserving file order.
    """
    keywords: list[str] = []
    if config_path is not None:
        keywords.extend(_load_keywords_from_path(Path(config_path)))
        return tuple(keywords)

    p = get_paths()
    default_legacy = getattr(p, "legacy_default_merchant_rules", None)
    for path in _unique_existing_paths(
        [
            p.merchant_rules,
            p.default_merchant_rules,
            default_legacy if isinstance(default_legacy, Path) else None,
        ]
    ):
        keywords.extend(_load_keywords_from_path(path))
    return tuple(keywords)
