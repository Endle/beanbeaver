"""Shared helpers for statement import workflows."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from beanbeaver.domain.beancount_dates import extract_dates_from_beancount
from beanbeaver.runtime import get_logger, get_paths

logger = get_logger(__name__)
_paths = get_paths()


def check_uncommitted_changes() -> bool:
    """Check if there are uncommitted changes in the repository."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=_paths.root,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def confirm_uncommitted_changes() -> bool:
    """Warn user about uncommitted changes and ask for confirmation."""
    if not check_uncommitted_changes():
        return True

    logger.warning("There are uncommitted changes in the repository.")
    print("Uncommitted changes detected. If import fails, you can revert with 'git checkout .'")
    print("Continue? [y/N] ", end="")
    response = input().strip().lower()
    if response != "y":
        logger.info("Aborted by user")
        return False
    return True


def detect_csv_files(
    patterns: list[tuple[str, Callable[[str], bool]]],
    file_type_name: str = "CSV",
    downloads_dir: Path | None = None,
) -> str | None:
    """
    Auto-detect CSV files in Downloads matching given patterns.

    Returns selected filename, or None if no match found.
    """
    downloads = downloads_dir or _paths.downloads
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

    if not sys.stdin.isatty():
        raise RuntimeError(
            f"Multiple {file_type_name} files found in ~/Downloads. "
            f"Run interactively or pass an explicit file: {', '.join(found_files)}"
        )

    print(f"Multiple {file_type_name} files found in ~/Downloads:")
    for i, fname in enumerate(found_files):
        print(f"  {i}: {fname}")
    print("Which file to import? ", end="")
    choice = input().strip()
    try:
        idx = int(choice)
        return found_files[idx]
    except (ValueError, IndexError):
        raise RuntimeError("Invalid file selection") from None


def select_interactive_item[T](
    options: Sequence[T],
    *,
    render: Callable[[T], str],
    heading: str,
    prompt: str,
    non_tty_error: str,
    invalid_choice_error: str,
) -> T:
    """
    Return one selected option from a list with TTY/non-TTY handling.

    Raises RuntimeError when no options exist, when non-interactive mode is
    unable to resolve multiple options, or when the selection is invalid.
    """
    if not options:
        raise RuntimeError("No options available for selection.")
    if len(options) == 1:
        return options[0]

    if not sys.stdin.isatty():
        rendered_options = ", ".join(render(option) for option in options)
        raise RuntimeError(f"{non_tty_error}: {rendered_options}")

    print(heading)
    for idx, option in enumerate(options, 1):
        print(f"  {idx}. {render(option)}")

    choice = input(prompt).strip()
    try:
        return options[int(choice) - 1]
    except (ValueError, IndexError):
        raise RuntimeError(invalid_choice_error) from None


def select_interactive_option(
    options: Sequence[str],
    *,
    heading: str,
    prompt: str,
    non_tty_error: str,
    invalid_choice_error: str,
) -> str:
    """String-specialized convenience wrapper around select_interactive_item."""
    return select_interactive_item(
        options,
        render=lambda value: value,
        heading=heading,
        prompt=prompt,
        non_tty_error=non_tty_error,
        invalid_choice_error=invalid_choice_error,
    )


def copy_statement_csv(
    csv_file: str,
    target_path: Path,
    *,
    downloads_dir: Path | None = None,
    allow_absolute: bool,
) -> Path:
    """
    Copy source CSV file to target path and return the resolved source path.

    If allow_absolute is true, falls back to interpreting csv_file as a path.
    """
    downloads = downloads_dir or _paths.downloads
    source_path = downloads / csv_file
    if not source_path.exists() and allow_absolute:
        source_path = Path(csv_file)

    if not source_path.exists():
        raise FileNotFoundError(csv_file)

    shutil.copyfile(source_path, target_path)
    return source_path


def detect_statement_date_range(
    content: str,
    *,
    start_date: str | None,
    end_date: str | None,
    include_balance: bool,
) -> tuple[str | None, str | None]:
    """
    Return explicit dates when provided, otherwise detect from Beancount content.
    """
    if start_date is not None and end_date is not None:
        return start_date, end_date
    return extract_dates_from_beancount(content, include_balance=include_balance)


def write_import_output(
    *,
    output_content: str,
    result_file_name: str,
    records_import_path: Path,
    yearly_summary_path: Path,
) -> Path:
    """
    Write import output to records directory and append include to yearly summary.
    """
    result_file_path = records_import_path / result_file_name
    records_import_path.mkdir(parents=True, exist_ok=True)

    with open(result_file_path, "w") as fout:
        fout.write(output_content)

    command = f'include "{result_file_name}"'
    summary_content = yearly_summary_path.read_text() if yearly_summary_path.exists() else ""
    if command not in summary_content:
        with open(yearly_summary_path, "a") as fout_sum:
            print(command, file=fout_sum)

    return result_file_path
