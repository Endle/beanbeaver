"""Runtime loader for receipt item categorization rules."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from beanbeaver.receipt.item_categories import ItemCategoryRuleLayers, build_item_category_rule_layers
from beanbeaver.runtime.paths import get_paths


def _load_toml(path: Path) -> dict[str, Any]:
    """Load TOML file and return parsed dict; missing files map to empty dict."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    if not path.exists():
        return {}

    with open(path, "rb") as f:
        data = tomllib.load(f)
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=8)
def load_item_category_rule_layers(
    classifier_paths: tuple[str, ...] | None = None,
    account_paths: tuple[str, ...] | None = None,
) -> ItemCategoryRuleLayers:
    """Load item-category rules from runtime-configured files into pure in-memory layers."""
    p = get_paths()

    if classifier_paths is None:
        module_default_rules = (
            Path(__file__).resolve().parents[1] / "receipt" / "rules" / "default_item_classifier.toml"
        )
        seen_paths: set[Path] = set()
        classifier_files: list[Path] = []
        for candidate in (module_default_rules, p.default_item_classifier_rules, p.item_classifier_rules):
            resolved = candidate.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            classifier_files.append(candidate)
    else:
        classifier_files = [Path(path) for path in classifier_paths]

    if account_paths is None:
        account_files = [p.item_category_accounts]
    else:
        account_files = [Path(path) for path in account_paths]

    classifier_configs = tuple(_load_toml(path) for path in classifier_files)
    account_configs = tuple(_load_toml(path) for path in account_files)
    return build_item_category_rule_layers(
        classifier_configs=classifier_configs,
        account_configs=account_configs,
    )
