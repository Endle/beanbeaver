"""Rule engine for merchant categorization.

This module provides a data-driven approach to categorize merchants
using TOML rules for simple keyword matching and Python functions
for complex logic.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from beanbeaver.runtime.logging import get_logger
from beanbeaver.runtime.paths import get_paths

logger = get_logger(__name__)


class CategorizationInput(Protocol):
    """Minimal input contract required by the rules engine."""

    raw_merchant_name: str


class RuleEngine:
    """Engine for categorizing transactions using TOML and Python rules.

    The engine processes rules in order:
    1. Python rules (for complex logic like amount-based decisions)
    2. TOML rules (simple keyword matching, first match wins)
    3. Default category if no match
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize the rule engine.

        Args:
            config_path: Path to the TOML config file. If None, uses default location.
        """
        if config_path is None:
            config_path = get_paths().merchant_rules

        self.toml_rules: list[dict[str, Any]] = self._load_toml(config_path)
        self.python_rules: list[Callable[[CategorizationInput], str | None]] = []
        logger.debug("Loaded %d TOML rules from %s", len(self.toml_rules), config_path)

    def _load_toml(self, config_path: Path) -> list[dict[str, Any]]:
        """Load and parse TOML rules file.

        Args:
            config_path: Path to the TOML file.

        Returns:
            List of rule dictionaries with 'keywords' and 'category' keys.
        """
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]

        if not config_path.exists():
            logger.warning("Config file not found: %s", config_path)
            return []

        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        rules = data.get("rules", [])

        # Normalize keywords to uppercase for case-insensitive matching
        for rule in rules:
            rule["keywords"] = [kw.upper() for kw in rule.get("keywords", [])]

        return rules

    def register_rule(self, rule_func: Callable[[CategorizationInput], str | None]) -> None:
        """Register a Python rule function.

        Python rules are executed before TOML rules and can implement
        complex logic like amount-based decisions or multi-condition checks.

        Args:
            rule_func: A function that takes a CardTransaction and returns
                       a category string or None if no match.
        """
        self.python_rules.append(rule_func)
        logger.debug("Registered Python rule: %s", rule_func.__name__)

    def register_rules(self, rule_funcs: list[Callable[[CategorizationInput], str | None]]) -> None:
        """Register multiple Python rule functions.

        Args:
            rule_funcs: List of rule functions to register.
        """
        for rule_func in rule_funcs:
            self.register_rule(rule_func)

    def categorize(self, txn: CategorizationInput) -> str:
        """Categorize a transaction using registered rules.

        Processing order:
        1. Python rules (first match wins)
        2. TOML rules (first match wins)
        3. Default: "Expenses:Uncategorized"

        Args:
            txn: The transaction to categorize.

        Returns:
            The expense category for the transaction.
        """
        # 1. Python rules first (complex logic, early exits)
        for rule in self.python_rules:
            result = rule(txn)
            if result:
                logger.debug(
                    "Python rule %s matched for '%s': %s",
                    rule.__name__,
                    txn.raw_merchant_name,
                    result,
                )
                return result

        # 2. TOML rules (simple keyword matching)
        merchant_upper = txn.raw_merchant_name.upper()
        for toml_rule in self.toml_rules:
            if any(kw in merchant_upper for kw in toml_rule["keywords"]):
                category = toml_rule["category"]
                logger.debug(
                    "TOML rule matched for '%s': %s (keyword: %s)",
                    txn.raw_merchant_name,
                    category,
                    [kw for kw in toml_rule["keywords"] if kw in merchant_upper][0],
                )
                return category

        # 3. Default
        logger.debug("No rule matched for '%s', using default", txn.raw_merchant_name)
        return "Expenses:Uncategorized"


# Global singleton instance
_engine: RuleEngine | None = None


def get_rule_engine(config_path: Path | None = None) -> RuleEngine:
    """Get or create the global rule engine instance.

    On first call, creates a new RuleEngine instance.
    Subsequent calls return the same instance (unless reset_rule_engine() is called).

    Args:
        config_path: Optional path to TOML config. Only used on first call.
                    Ignored if engine already exists.

    Returns:
        The singleton RuleEngine instance.
    """
    global _engine
    if _engine is None:
        _engine = RuleEngine(config_path=config_path)
    return _engine


def reset_rule_engine() -> None:
    """Reset the global rule engine instance.

    This clears the singleton, so the next call to get_rule_engine()
    will create a fresh instance. Useful for testing.
    """
    global _engine
    _engine = None


def create_rule_engine(config_path: Path | None = None, register_python_rules: bool = True) -> RuleEngine:
    """Create a new RuleEngine instance (not the singleton).

    Use this when you need an isolated engine instance, e.g., for testing
    or when you want different configuration than the global instance.

    Args:
        config_path: Path to TOML config file. If None, uses default.
        register_python_rules: Reserved for compatibility. No bundled Python
            rules are currently provided.

    Returns:
        A new RuleEngine instance.
    """
    engine = RuleEngine(config_path=config_path)
    if register_python_rules:
        logger.warning(
            "register_python_rules=True requested, but no Python rules are bundled. "
            "Use RuleEngine.register_rules() to add custom rules."
        )
    return engine
