"""Shared helpers for CLI orchestrator commands."""

import subprocess
import sys
from collections.abc import Callable

from beanbeaver.runtime import TMPDIR, get_logger, get_paths

# Re-export TMPDIR for backward compatibility
TMPDIR = TMPDIR

logger = get_logger(__name__)

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


def check_uncommitted_changes() -> bool:
    """Check if there are uncommitted changes in the repository."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=BC_BASE_PATH,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def confirm_uncommitted_changes() -> None:
    """Warn user about uncommitted changes and ask for confirmation."""
    if not check_uncommitted_changes():
        return

    logger.warning("There are uncommitted changes in the repository.")
    print("Uncommitted changes detected. If import fails, you can revert with 'git checkout .'")
    print("Continue? [y/N] ", end="")
    response = input().strip().lower()
    if response != "y":
        logger.info("Aborted by user")
        sys.exit(0)


def detect_csv_files(
    patterns: list[tuple[str, Callable[[str], bool]]],
    file_type_name: str = "CSV",
) -> str | None:
    """
    Auto-detect CSV files in ~/Downloads matching given patterns.

    Args:
        patterns: List of (pattern_name, matcher_function) tuples
        file_type_name: Name to use in prompts (e.g., "credit card CSV", "chequing CSV")

    Returns:
        The selected filename, or None if no matches found
    """
    downloads = DOWNLOADED_CSV_BASE_PATH
    if not downloads.exists():
        return None

    found_files: list[str] = []
    for csv_file in downloads.iterdir():
        if not csv_file.is_file():
            continue
        fname = csv_file.name
        for pattern_name, matcher in patterns:
            if matcher(fname):
                found_files.append(fname)
                logger.debug("Found matching file: %s (pattern: %s)", fname, pattern_name)
                break

    if not found_files:
        return None

    if len(found_files) == 1:
        logger.info("Auto-detected CSV file: %s", found_files[0])
        return found_files[0]

    # Multiple files found - let user choose
    print(f"Multiple {file_type_name} files found in ~/Downloads:")
    for i, fname in enumerate(found_files):
        print(f"  {i}: {fname}")
    print("Which file to import? ", end="")
    choice = int(input())
    return found_files[choice]
