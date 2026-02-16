"""Centralized path management for the beancount project.

This module provides a single source of truth for all project paths,
eliminating scattered path definitions across modules.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def _get_project_root() -> Path:
    """Determine the project root directory."""
    # TODO should be able to search main.beancount
    # vendor/beanbeaver/runtime/paths.py -> vendor/beanbeaver/runtime -> vendor/beanbeaver -> vendor -> project root
    return Path(__file__).parent.parent.parent.parent


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
        """Bean Beaver code directory (vendor/beanbeaver/)."""
        return self.root / "vendor" / "beanbeaver"

    # --- Configuration paths ---
    @property
    def config(self) -> Path:
        """Configuration directory (config/)."""
        return self.root / "config"

    @property
    def merchant_rules(self) -> Path:
        """Merchant categorization rules TOML file."""
        return self.config / "merchant_rules.toml"

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
        """Vendor default receipt item classifier rules TOML file."""
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
    def receipts_approved(self) -> Path:
        """Approved receipts awaiting CC match."""
        return self.receipts / "approved"

    @property
    def receipts_matched(self) -> Path:
        """Receipts successfully merged into CC imports."""
        return self.receipts / "matched"

    @property
    def receipts_images(self) -> Path:
        """Receipt photos/images."""
        return self.receipts / "images"

    @property
    def receipts_scanned(self) -> Path:
        """Scanned receipts awaiting manual review."""
        return self.receipts / "scanned"

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
        self.receipts_approved.mkdir(parents=True, exist_ok=True)
        self.receipts_matched.mkdir(parents=True, exist_ok=True)
        self.receipts_images.mkdir(parents=True, exist_ok=True)
        self.receipts_scanned.mkdir(parents=True, exist_ok=True)
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
