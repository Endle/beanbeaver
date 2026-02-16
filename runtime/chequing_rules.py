"""Runtime loader for chequing transaction categorization rules."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from beanbeaver.runtime.paths import get_paths


@lru_cache(maxsize=4)
def load_chequing_categorization_patterns(config_path: str | None = None) -> tuple[tuple[str, str], ...]:
    """
    Load chequing categorization patterns from TOML.

    Returns:
        Tuple of (pattern, account) pairs preserving file order.
    """
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    path = Path(config_path) if config_path is not None else get_paths().chequing_rules
    if not path.exists():
        raise FileNotFoundError(f"Chequing rules file not found: {path}")

    with open(path, "rb") as f:
        config = tomllib.load(f)

    patterns: list[tuple[str, str]] = []
    for rule in config.get("rules", []):
        pattern = str(rule.get("pattern", "")).strip()
        account = str(rule.get("account", "")).strip()
        if pattern and account:
            patterns.append((pattern.upper(), account))

    if not patterns:
        raise ValueError(f"No valid chequing categorization rules found in {path}")

    return tuple(patterns)
