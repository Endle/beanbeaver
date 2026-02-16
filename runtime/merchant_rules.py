"""Runtime loader for merchant categorization rules."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from beanbeaver.runtime.paths import get_paths


@lru_cache(maxsize=4)
def load_known_merchant_keywords(config_path: str | None = None) -> tuple[str, ...]:
    """
    Load known merchant keywords from merchant_rules.toml.

    Args:
        config_path: Optional TOML path override. If None, uses default project path.

    Returns:
        Tuple of merchant keywords from all rules, preserving file order.
    """
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    path = Path(config_path) if config_path is not None else get_paths().merchant_rules
    if not path.exists():
        return tuple()

    with open(path, "rb") as f:
        config = tomllib.load(f)

    keywords: list[str] = []
    for rule in config.get("rules", []):
        keywords.extend(rule.get("keywords", []))
    return tuple(keywords)
