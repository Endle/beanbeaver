"""Centralized path management for the beancount project.

This module provides a single source of truth for all project paths,
eliminating scattered path definitions across modules.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


_PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _is_host_project_root(path: Path) -> bool:
    """Return True when a directory looks like a beancount project root."""
    markers = (
        path / "main.beancount",
        path / "accounts.beancount",
        path / "records",
        path / "receipts",
        path / "config",
    )
    return any(marker.exists() for marker in markers)


def _search_upwards(start: Path) -> Path | None:
    """Search start and its parents for a host project root."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if _is_host_project_root(candidate):
            return candidate
    return None


def _get_project_root() -> Path:
    """Determine the active beancount project root directory."""
    env_root = os.environ.get("BEANBEAVER_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()

    # Vendored layout: <project>/vendor/beanbeaver/runtime/paths.py
    if _PACKAGE_ROOT.parent.name == "vendor":
        return _PACKAGE_ROOT.parent.parent.resolve()

    cwd_root = _search_upwards(Path.cwd())
    if cwd_root is not None:
        return cwd_root

    # Standalone/editable layout: treat the package checkout itself as the root.
    return _PACKAGE_ROOT.resolve()


@dataclass
class ProjectPaths:
    """Container for all project-related paths.

    All paths are computed relative to the project root, ensuring consistency
    across all modules regardless of the current working directory.
    """

    root: Path = field(default_factory=_get_project_root)

    # Current year for imports (can be overridden)
    current_year: str = "2026"

    def __post_init__(self) -> None:
        # Ensure root is resolved to absolute path
        self.root = self.root.resolve()

    # --- Source code paths ---
    @property
    def src(self) -> Path:
        """Bean Beaver code directory.

        Supports both:
        - host project layout: <root>/vendor/beanbeaver/
        - standalone beanbeaver layout: <root>/
        """
        vendored = self.root / "vendor" / "beanbeaver"
        if vendored.exists():
            return vendored
        return _PACKAGE_ROOT

    # --- Configuration paths ---
    @property
    def config(self) -> Path:
        """Configuration directory (config/)."""
        return self.root / "config"

    @property
    def merchant_rules(self) -> Path:
        """Project-local merchant categorization rules TOML file."""
        return self.config / "merchant_rules.toml"

    @property
    def rules(self) -> Path:
        """Shared default rules directory."""
        return self.src / "rules"

    @property
    def default_merchant_rules(self) -> Path:
        """Default merchant categorization rules TOML file (canonical)."""
        return self.rules / "default_merchant_rules.toml"

    @property
    def legacy_default_merchant_rules(self) -> Path:
        """Legacy default merchant rules path."""
        return self.src / "runtime" / "rules" / "default_merchant_rules.toml"

    @property
    def chequing_rules(self) -> Path:
        """Chequing transaction categorization rules TOML file."""
        return self.config / "chequing_rules.toml"

    @property
    def item_classifier_rules(self) -> Path:
        """Project-level receipt item classifier rules TOML file."""
        return self.config / "item_classifier.toml"

    @property
    def item_category_accounts(self) -> Path:
        """Project-level item key -> beancount account mapping TOML file."""
        return self.config / "item_category_accounts.toml"

    @property
    def default_item_classifier_rules(self) -> Path:
        """Default receipt item classifier rules TOML file (canonical)."""
        return self.rules / "default_item_classifier.toml"

    @property
    def legacy_default_item_classifier_rules(self) -> Path:
        """Legacy default receipt item classifier rules path."""
        return self.src / "receipt" / "rules" / "default_item_classifier.toml"

    # --- Records/ledger paths ---
    @property
    def records(self) -> Path:
        """Records directory containing beancount files by year."""
        return self.root / "records"

    @property
    def records_current_year(self) -> Path:
        """Records directory for the current year."""
        return self.records / self.current_year

    @property
    def yearly_summary(self) -> Path:
        """Current year's main summary beancount file."""
        return self.records_current_year / f"{self.current_year}.beancount"

    @property
    def main_beancount(self) -> Path:
        """Main beancount entry file."""
        return self.root / "main.beancount"

    @property
    def accounts_beancount(self) -> Path:
        """Account definitions file."""
        return self.root / "accounts.beancount"

    # --- Receipt paths ---
    @property
    def receipts(self) -> Path:
        """Root receipts directory."""
        return self.root / "receipts"

    @property
    def receipts_json(self) -> Path:
        """Root staged receipt JSON directory."""
        return self.receipts / "json"

    @property
    def receipts_json_scanned(self) -> Path:
        """Parsed receipt JSON awaiting manual review."""
        return self.receipts_json / "scanned"

    @property
    def receipts_json_approved(self) -> Path:
        """Reviewed receipt JSON awaiting CC match."""
        return self.receipts_json / "approved"

    @property
    def receipts_json_matched(self) -> Path:
        """Receipt JSON already matched into the ledger."""
        return self.receipts_json / "matched"

    @property
    def receipts_rendered(self) -> Path:
        """Root rendered receipt output directory."""
        return self.receipts / "rendered"

    @property
    def receipts_rendered_scanned(self) -> Path:
        """Rendered Beancount output for scanned receipts."""
        return self.receipts_rendered / "scanned"

    @property
    def receipts_rendered_approved(self) -> Path:
        """Rendered Beancount output for approved receipts."""
        return self.receipts_rendered / "approved"

    @property
    def receipts_rendered_matched(self) -> Path:
        """Rendered Beancount output for matched receipts."""
        return self.receipts_rendered / "matched"

    @property
    def receipts_approved(self) -> Path:
        """Compatibility alias for approved receipt JSON directory."""
        return self.receipts_json_approved

    @property
    def receipts_matched(self) -> Path:
        """Compatibility alias for matched receipt JSON directory."""
        return self.receipts_json_matched

    @property
    def receipts_images(self) -> Path:
        """Receipt photos/images."""
        return self.receipts / "images"

    @property
    def receipts_scanned(self) -> Path:
        """Compatibility alias for scanned receipt JSON directory."""
        return self.receipts_json_scanned

    @property
    def receipts_ocr_json(self) -> Path:
        """Raw OCR results (JSON)."""
        return self.receipts / "ocr_json"

    # --- External paths ---
    @property
    def downloads(self) -> Path:
        """User's Downloads directory for CSV imports."""
        return Path("~/Downloads").expanduser()

    def ensure_receipt_directories(self) -> None:
        """Create all receipt-related directories if they don't exist."""
        self.receipts_json_scanned.mkdir(parents=True, exist_ok=True)
        self.receipts_json_approved.mkdir(parents=True, exist_ok=True)
        self.receipts_json_matched.mkdir(parents=True, exist_ok=True)
        self.receipts_rendered_scanned.mkdir(parents=True, exist_ok=True)
        self.receipts_rendered_approved.mkdir(parents=True, exist_ok=True)
        self.receipts_rendered_matched.mkdir(parents=True, exist_ok=True)
        self.receipts_images.mkdir(parents=True, exist_ok=True)
        self.receipts_ocr_json.mkdir(parents=True, exist_ok=True)


# Module-level singleton and temporary directory
_paths: ProjectPaths | None = None
_tmpdir = tempfile.TemporaryDirectory()
TMPDIR = Path(_tmpdir.name)


def get_paths() -> ProjectPaths:
    """Get the singleton ProjectPaths instance.

    Returns:
        The global ProjectPaths instance.
    """
    global _paths
    if _paths is None:
        _paths = ProjectPaths()
    return _paths


def set_current_year(year: str) -> None:
    """Update the current year for path resolution.

    Args:
        year: The year string (e.g., "2026").
    """
    get_paths().current_year = year


# Convenience exports for backwards compatibility
# These mirror the old common.py exports
def _get_compat_paths() -> tuple[Path, ...]:
    """Get paths for backwards compatibility with common.py imports."""
    p = get_paths()
    return (
        p.downloads,  # DOWNLOADED_CSV_BASE_PATH
        p.root,  # BC_BASE_PATH
        p.src,  # BC_CODE_PATH
        p.records,  # BC_RECORD_PATH
        p.records_current_year,  # BC_RECORD_IMPORT_PATH
        p.yearly_summary,  # BC_YEARLY_SUMMARY_PATH
        p.main_beancount,  # MAIN_BEANCOUNT_PATH
        p.accounts_beancount,  # ACCOUNT_LIST_PATH
    )
