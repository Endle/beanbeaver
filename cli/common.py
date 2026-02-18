"""Shared helpers for CLI orchestrator commands."""

from collections.abc import Callable

from beanbeaver.application.imports.shared import (
    check_uncommitted_changes as _check_uncommitted_changes,
)
from beanbeaver.application.imports.shared import (
    confirm_uncommitted_changes as _confirm_uncommitted_changes,
)
from beanbeaver.application.imports.shared import (
    detect_csv_files as _detect_csv_files,
)
from beanbeaver.runtime import TMPDIR, get_paths

# Re-export TMPDIR for backward compatibility
TMPDIR = TMPDIR

# Re-export path constants for backward compatibility
# New code should use: from beanbeaver.runtime import get_paths
_p = get_paths()
CURRENT_YEAR = _p.current_year
DOWNLOADED_CSV_BASE_PATH = _p.downloads
BC_BASE_PATH = _p.root
BC_CODE_PATH = _p.src
BC_RECORD_PATH = _p.records
BC_RECORD_IMPORT_PATH = _p.records_current_year
BC_YEARLY_SUMMARY_PATH = _p.yearly_summary
MAIN_BEANCOUNT_PATH = _p.main_beancount
ACCOUNT_LIST_PATH = _p.accounts_beancount

# Backward-compatible re-exports.
check_uncommitted_changes = _check_uncommitted_changes
confirm_uncommitted_changes = _confirm_uncommitted_changes


def detect_csv_files(
    patterns: list[tuple[str, Callable[[str], bool]]],
    file_type_name: str = "CSV",
) -> str | None:
    """Backward-compatible wrapper around application import shared helper."""
    return _detect_csv_files(
        patterns,
        file_type_name=file_type_name,
        downloads_dir=DOWNLOADED_CSV_BASE_PATH,
    )
