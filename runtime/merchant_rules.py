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
    keywords.extend(_load_keywords_from_path(p.merchant_rules))
    keywords.extend(_load_keywords_from_path(p.default_merchant_rules))
    return tuple(keywords)
