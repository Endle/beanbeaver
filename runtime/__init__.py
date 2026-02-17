"""Runtime infrastructure for the beancount project.

This package provides process/runtime services including:
- Logging setup via get_logger()
- Path resolution via get_paths(), ProjectPaths, TMPDIR
- Rule engine construction via create_rule_engine(), get_rule_engine()

Usage:
    from beanbeaver.runtime import get_logger, get_paths, ProjectPaths, TMPDIR

    logger = get_logger(__name__)
    paths = get_paths()
    print(paths.root, paths.records)
"""

from beanbeaver.runtime.chequing_rules import load_chequing_categorization_patterns
from beanbeaver.runtime.item_category_rules import load_item_category_rule_layers
from beanbeaver.runtime.logging import (
    DEFAULT_LOG_LEVEL,
    LOG_FORMAT,
    LOG_FORMAT_DEBUG,
    configure_logging,
    get_logger,
    set_log_level,
)
from beanbeaver.runtime.merchant_rules import load_known_merchant_keywords
from beanbeaver.runtime.paths import (
    TMPDIR,
    ProjectPaths,
    get_paths,
    set_current_year,
)
from beanbeaver.runtime.rule_engine import (
    RuleEngine,
    create_rule_engine,
    get_rule_engine,
    reset_rule_engine,
)

__all__ = [
    # Logging
    "get_logger",
    "configure_logging",
    "set_log_level",
    "DEFAULT_LOG_LEVEL",
    "LOG_FORMAT",
    "LOG_FORMAT_DEBUG",
    # Rules
    "load_known_merchant_keywords",
    "load_item_category_rule_layers",
    "load_chequing_categorization_patterns",
    "RuleEngine",
    "get_rule_engine",
    "reset_rule_engine",
    "create_rule_engine",
    # Paths
    "get_paths",
    "set_current_year",
    "ProjectPaths",
    "TMPDIR",
]
