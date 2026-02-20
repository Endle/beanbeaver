"""Chequing account import workflow.

This module contains the logic for importing chequing account CSV files,
extracted from the original process_chequing.py script.
"""

import argparse
import datetime
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from beanbeaver.application.imports.account_discovery import find_open_accounts, resolve_cc_payment_account
from beanbeaver.application.imports.csv_routing import detect_chequing_csv as detect_chequing_csv_by_rules
from beanbeaver.application.imports.shared import (
    confirm_uncommitted_changes,
    copy_statement_csv,
    detect_statement_date_range,
    select_interactive_option,
    write_import_output,
)
from beanbeaver.domain.chequing_categorization import categorize_chequing_transaction
from beanbeaver.domain.chequing_import import (
    build_result_file,
    format_balance,
    format_transaction,
    latest_date,
    parse_eqbank_rows,
    parse_scotia_rows,
)
from beanbeaver.ledger_access import transaction_dates_for_account, validate_ledger
from beanbeaver.runtime import TMPDIR, get_logger, get_paths, load_chequing_categorization_patterns

logger = get_logger(__name__)

# Get paths
_paths = get_paths()
DOWNLOADED_CSV_BASE_PATH = _paths.downloads
BC_RECORD_IMPORT_PATH = _paths.records_current_year
BC_YEARLY_SUMMARY_PATH = _paths.yearly_summary
MAIN_BEANCOUNT_PATH = _paths.main_beancount

EQBANK_ACCOUNT_PATTERNS = [
    "Assets:Bank:Chequing:EQBank*",
    "Assets:Bank:Chequing:*EQBank*",
]

SCOTIA_ACCOUNT_PATTERNS = [
    "Assets:Bank:Chequing:Scotia*",
    "Assets:Bank:Chequing:*Scotia*",
]

ChequingImportStatus = Literal["ok", "aborted", "error"]


@dataclass(frozen=True)
class ChequingImportRequest:
    """Inputs for chequing statement import workflow."""

    csv_file: str | None = None


@dataclass(frozen=True)
class ChequingImportResult:
    """Outcome for chequing statement import workflow."""

    status: ChequingImportStatus
    result_file_path: Path | None = None
    result_file_name: str | None = None
    account: str | None = None
    chequing_type: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    error: str | None = None


def detect_chequing_csv() -> str | None:
    """Auto-detect a chequing CSV file (EQ Bank or Scotia) in ~/Downloads."""
    return detect_chequing_csv_by_rules(DOWNLOADED_CSV_BASE_PATH)


def detect_chequing_type(csv_path: Path) -> str:
    """Detect chequing CSV type based on header columns."""
    import csv

    with open(csv_path, encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        headers = [h or "" for h in (reader.fieldnames or [])]

    if "Transfer date" in headers and "Amount" in headers and "Balance" in headers:
        return "eqbank"
    if "Type of Transaction" in headers and "Sub-description" in headers:
        return "scotia"
    raise ValueError("Unrecognized chequing CSV format")


def get_existing_transaction_dates(account: str) -> set[datetime.date]:
    """
    Parse existing ledger and find all dates with transactions for the given account.

    Uses privileged ledger reader to find transaction dates for the account.
    """
    existing_dates = transaction_dates_for_account(account, ledger_path=MAIN_BEANCOUNT_PATH)
    logger.info("Found %d existing transaction dates for %s", len(existing_dates), account)
    return existing_dates


def _select_chequing_account(
    patterns: list[str],
    *,
    label: str,
    as_of: datetime.date | None,
) -> str:
    matches = find_open_accounts(patterns, as_of=as_of)
    if not matches:
        raise RuntimeError(f"No open {label} accounts found in main ledger.")

    return select_interactive_option(
        matches,
        heading=f"Multiple {label} accounts found:",
        prompt="Select account (number): ",
        non_tty_error=f"Multiple {label} accounts found. Run interactively to choose",
        invalid_choice_error="Invalid account selection",
    )


def parse_chequing_request(argv: Sequence[str] | None = None) -> ChequingImportRequest:
    """Parse CLI args into a typed request object."""
    parser = argparse.ArgumentParser(description="Import chequing transactions")
    parser.add_argument("csv_file", nargs="?", help="CSV file to import (auto-detect if omitted)")
    args = parser.parse_args(argv)
    return ChequingImportRequest(csv_file=args.csv_file)


def run_chequing_import(request: ChequingImportRequest) -> ChequingImportResult:
    """Run chequing import workflow and return structured result."""
    if not confirm_uncommitted_changes():
        return ChequingImportResult(status="aborted")

    csv_file = request.csv_file
    if not csv_file:
        try:
            csv_file = detect_chequing_csv()
        except RuntimeError as exc:
            return ChequingImportResult(status="error", error=str(exc))
        if csv_file is None:
            return ChequingImportResult(
                status="error",
                error="No chequing CSV file found in ~/Downloads\nSupported files: *Details.csv, Preferred_Package_*.csv",
            )

    logger.info("Importing chequing transactions from: %s", csv_file)
    target_file_name = TMPDIR / "chequing.csv"
    try:
        copy_statement_csv(
            csv_file=csv_file,
            target_path=target_file_name,
            downloads_dir=DOWNLOADED_CSV_BASE_PATH,
            allow_absolute=True,
        )
    except FileNotFoundError:
        return ChequingImportResult(status="error", error=f"File not found: {csv_file}")

    try:
        chequing_type = detect_chequing_type(target_file_name)
    except ValueError as exc:
        return ChequingImportResult(status="error", error=str(exc))

    source_label = "EQ Bank Chequing" if chequing_type == "eqbank" else "Scotia Chequing"
    logger.info("Detected chequing type: %s", chequing_type)

    # Read CSV once for parsing + account selection
    import csv

    with open(target_file_name, encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        rows = list(reader)

    if chequing_type == "eqbank":
        parsed_rows = parse_eqbank_rows(rows)
        as_of = latest_date(parsed_rows)
        account = _select_chequing_account(
            EQBANK_ACCOUNT_PATTERNS,
            label="EQ Bank chequing",
            as_of=as_of,
        )
        from beanbeaver.importers.eqbank import EQBankChequingImporter

        importer = EQBankChequingImporter(account=account)
    else:
        parsed_rows = parse_scotia_rows(rows)
        as_of = latest_date(parsed_rows)
        account = _select_chequing_account(
            SCOTIA_ACCOUNT_PATTERNS,
            label="Scotia chequing",
            as_of=as_of,
        )
        from beanbeaver.importers.scotia_chequing import ScotiaChequingImporter

        importer = ScotiaChequingImporter(account=account)

    class FileMemo:
        def __init__(self, name: str):
            self.name = name

    f = FileMemo(str(target_file_name))
    transactions, balances = importer.extract_with_balances(f)
    logger.info("Extracted %d transactions", len(transactions))
    logger.info("Extracted %d balance entries", len(balances))

    existing_dates = get_existing_transaction_dates(account)
    new_txn_dates = {txn.date for txn in transactions}

    # Deduplicate balances by date - keep the FIRST balance for each date
    balance_by_date: dict[datetime.date, Decimal] = {}
    for balance_date, balance_amount in balances:
        if balance_date not in balance_by_date:
            balance_by_date[balance_date] = balance_amount

    # Filter balance dates - only emit Balance for dates without transactions
    filtered_balances: list[tuple[datetime.date, Decimal]] = []
    for balance_date, balance_amount in sorted(balance_by_date.items()):
        if balance_date not in existing_dates and balance_date not in new_txn_dates:
            filtered_balances.append((balance_date, balance_amount))
        else:
            logger.debug("Skipping balance for %s (has transactions)", balance_date)

    logger.info("Filtered to %d balance entries on 'quiet' days", len(filtered_balances))
    categorization_patterns = load_chequing_categorization_patterns()

    output_lines = [
        ";; -*- mode: beancount -*-",
        f";; {source_label} Import from {csv_file}",
        "",
    ]

    # Process transactions - sorted by date
    entries: list[tuple[datetime.date, str]] = []
    cc_cache: dict[str, str | None] = {}
    for date, description, amount_val, _balance_val in parsed_rows:
        cc_account = resolve_cc_payment_account(
            description,
            as_of=as_of,
            cache=cc_cache,
            txn_date=date,
            amount=f"{amount_val} CAD",
        )
        if cc_account:
            expense_account = cc_account
        else:
            category = categorize_chequing_transaction(description, patterns=categorization_patterns)
            expense_account = category if category else "Expenses:Uncategorized"

        txn_text = format_transaction(date, description, amount_val, account, expense_account)
        entries.append((date, txn_text))

    for balance_date, balance_amount in filtered_balances:
        balance_text = format_balance(balance_date, account, balance_amount)
        entries.append((balance_date, balance_text))

    entries.sort(key=lambda x: x[0])
    output_lines.extend(entry_text for _, entry_text in entries)
    output_content = "\n".join(output_lines)

    start_date, end_date = detect_statement_date_range(
        output_content,
        start_date=None,
        end_date=None,
        include_balance=True,
    )
    if not start_date or not end_date:
        return ChequingImportResult(status="error", error="Could not auto-detect dates from transactions")

    logger.info("Date range: %s - %s", start_date, end_date)
    result_file_name = build_result_file(start_date, end_date, chequing_type)
    result_file_path = write_import_output(
        output_content=output_content,
        result_file_name=result_file_name,
        records_import_path=BC_RECORD_IMPORT_PATH,
        yearly_summary_path=BC_YEARLY_SUMMARY_PATH,
    )
    logger.info("Writing result to: %s", result_file_path)
    logger.info("Including %s in yearly summary", result_file_name)

    logger.info("Validating ledger...")
    validation_errors = validate_ledger(ledger_path=MAIN_BEANCOUNT_PATH)
    if validation_errors:
        logger.error("Ledger validation found errors:")
        for err in validation_errors[:20]:
            print(err)
        if len(validation_errors) > 20:
            print(f"... and {len(validation_errors) - 20} more")
    else:
        logger.info("Validation passed!")

    print(f"\nImport complete: {result_file_path}")
    return ChequingImportResult(
        status="ok",
        result_file_path=result_file_path,
        result_file_name=result_file_name,
        account=account,
        chequing_type=chequing_type,
        start_date=start_date,
        end_date=end_date,
    )


def main(argv: Sequence[str] | None = None) -> int:
    request = parse_chequing_request(argv)
    result = run_chequing_import(request)
    if result.status == "ok":
        return 0
    if result.status == "aborted":
        logger.info("Import aborted by user.")
        return 0
    assert result.error is not None
    for line in result.error.splitlines():
        logger.error(line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
