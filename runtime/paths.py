"""Centralized path management for the beancount project.

This module provides a single source of truth for all project paths,
eliminating scattered path definitions across modules.
"""

from __future__ import annotations

import json
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
    )
    return any(marker.exists() for marker in markers)


def _search_upwards(start: Path) -> Path | None:
    """Search start and its parents for a host project root."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if _is_host_project_root(candidate):
            return candidate
    return None


def _expand_downloads_env(raw: str) -> Path:
    """Expand common shell placeholders in download-directory env vars."""
    expanded = raw.replace("$HOME", str(Path.home())).replace("${HOME}", str(Path.home()))
    return Path(os.path.expandvars(expanded)).expanduser()


def _default_downloads_path() -> Path:
    """Return a best-effort Downloads directory across supported platforms."""
    override = os.environ.get("BEANBEAVER_DOWNLOADS", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    xdg_downloads = os.environ.get("XDG_DOWNLOAD_DIR", "").strip()
    if xdg_downloads:
        return _expand_downloads_env(xdg_downloads).resolve()

    home = Path.home()
    candidates = [home / "Downloads"]

    onedrive = os.environ.get("OneDrive", "").strip()
    if onedrive:
        candidates.append(Path(onedrive) / "Downloads")

    userprofile = os.environ.get("USERPROFILE", "").strip()
    if userprofile:
        candidates.append(Path(userprofile) / "Downloads")

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].expanduser().resolve()


def bootstrap_tui_config_path() -> Path:
    """Return the bootstrap config path used to discover the active project root."""
    return _PACKAGE_ROOT / "config" / "tui.json"


def _load_bootstrap_tui_config() -> dict[str, object]:
    """Load bootstrap TUI config if present, otherwise return an empty mapping."""
    config_path = bootstrap_tui_config_path()
    if not config_path.exists():
        return {}

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}
    return data


def _resolve_from_package_root(raw_path: str) -> Path:
    """Resolve a config path relative to the package checkout when needed."""
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = _PACKAGE_ROOT / candidate
    return candidate.resolve()


def _configured_project_root_from_bootstrap() -> Path | None:
    """Resolve an optional project-root override from bootstrap config."""
    config = _load_bootstrap_tui_config()

    project_root = config.get("project_root")
    if isinstance(project_root, str) and project_root.strip():
        return _resolve_from_package_root(project_root.strip())

    # Backward compatibility: derive the project root from the old ledger-path key.
    legacy_main = config.get("main_beancount_path")
    if isinstance(legacy_main, str) and legacy_main.strip():
        return _resolve_from_package_root(legacy_main.strip()).parent

    return None


def _get_project_root() -> Path:
    """Determine the active beancount project root directory."""
    env_root = os.environ.get("BEANBEAVER_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()

    configured_root = _configured_project_root_from_bootstrap()
    if configured_root is not None:
        return configured_root

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
    def merchant_families(self) -> Path:
        """Project-local merchant identity/family TOML file."""
        return self.config / "merchant_families.toml"

    @property
    def rules(self) -> Path:
        """Shared default rules directory."""
        return self.src / "rules"

    @property
    def default_merchant_families(self) -> Path:
        """Default merchant identity/family rules TOML file."""
        return self.rules / "default_merchant_families.toml"

    @property
    def legacy_default_merchant_families(self) -> Path:
        """Legacy default merchant family rules path."""
        return self.src / "runtime" / "rules" / "default_merchant_families.toml"

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
        env_override = os.environ.get("BEANBEAVER_MAIN_BEANCOUNT", "").strip()
        if env_override:
            return Path(env_override).expanduser().resolve()
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
        return _default_downloads_path()

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


def reset_paths() -> None:
    """Clear the cached ProjectPaths singleton so config/env changes take effect."""
    global _paths
    _paths = None


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
