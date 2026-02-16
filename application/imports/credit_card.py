"""Credit card import workflow."""

import datetime
import io
import os
import sys
from pathlib import Path

from beanbeaver.application.imports.account_discovery import find_open_accounts
from beanbeaver.application.imports.csv_routing import (
    detect_credit_card_csv as detect_credit_card_csv_by_rules,
)
from beanbeaver.application.imports.csv_routing import (
    detect_credit_card_importer_id,
)
from beanbeaver.application.imports.shared import (
    confirm_uncommitted_changes,
    copy_statement_csv,
    detect_statement_date_range,
    write_import_output,
)
from beanbeaver.domain.cc_import import build_result_file
from beanbeaver.importers import (
    AmexImporter,
    BaseCardImporter,
    BmoImporter,
    CanadianTireFinancialImporter,
    CibcImporter,
    MbnaImporter,
    PcfImporter,
    RogersImporter,
    ScotiaImporter,
)
from beanbeaver.runtime import TMPDIR, get_logger, get_paths

logger = get_logger(__name__)

# Get paths
_paths = get_paths()
DOWNLOADED_CSV_BASE_PATH = _paths.downloads
BC_RECORD_IMPORT_PATH = _paths.records_current_year
BC_YEARLY_SUMMARY_PATH = _paths.yearly_summary

CIBC_ACCOUNT_PATTERNS = ["Liabilities:CreditCard:CIBC*"]
BMO_ACCOUNT_PATTERNS = ["Liabilities:CreditCard:BMO*", "Liabilities:CreditCard:*:BMO:*", "Liabilities:CreditCard:*BMO*"]
SCOTIA_ACCOUNT_PATTERNS = ["Liabilities:CreditCard:Scotia*"]
ROGERS_ACCOUNT_PATTERNS = ["Liabilities:CreditCard:Rogers*"]
MBNA_ACCOUNT_PATTERNS = ["Liabilities:CreditCard:MBNA*"]
PCF_ACCOUNT_PATTERNS = ["Liabilities:CreditCard:PCFinancial*", "Liabilities:CreditCard:PC*"]
CTFS_ACCOUNT_PATTERNS = ["Liabilities:CreditCard:CTFS*"]
AMEX_ACCOUNT_PATTERNS = ["Liabilities:CreditCard:Amex*", "Liabilities:CreditCard:AmericanExpress*"]


def detect_credit_card_csv() -> str | None:
    """Auto-detect a credit card CSV file in ~/Downloads."""
    return detect_credit_card_csv_by_rules(DOWNLOADED_CSV_BASE_PATH)


class _FileMemo:
    def __init__(self, name: str) -> None:
        self.name = name


def _contains_token(account: str, token: str) -> bool:
    normalized = account.replace("-", "").replace("_", "").replace(" ", "").upper()
    normalized_token = token.replace("-", "").replace("_", "").replace(" ", "").upper()
    return normalized_token in normalized


def _select_account(
    matches: list[str],
    *,
    account_label: str,
    as_of: datetime.date | None,
) -> str:
    if not matches:
        raise RuntimeError(f"No open {account_label} accounts found in main ledger.")
    if len(matches) == 1:
        return matches[0]
    if not sys.stdin.isatty():
        as_of_text = as_of.isoformat() if as_of else "today"
        raise RuntimeError(
            f"Multiple open {account_label} accounts found as of {as_of_text}. "
            f"Run interactively to choose: {', '.join(matches)}"
        )

    print(f"Multiple open {account_label} accounts found:")
    for idx, account in enumerate(matches, 1):
        print(f"  {idx}. {account}")
    choice = input("Select account (number): ").strip()
    try:
        return matches[int(choice) - 1]
    except (ValueError, IndexError):
        raise RuntimeError("Invalid account selection") from None


def _build_detection_importers() -> list[BaseCardImporter]:
    # Accounts are placeholders here; only identify()/date parsing is used.
    return [
        CibcImporter(account="Liabilities:CreditCard:Tmp:CIBC", simplii_account="Liabilities:CreditCard:Tmp:Simplii"),
        BmoImporter(account="Liabilities:CreditCard:Tmp:BMO"),
        ScotiaImporter(account="Liabilities:CreditCard:Tmp:Scotia"),
        MbnaImporter(account="Liabilities:CreditCard:Tmp:MBNA"),
        PcfImporter(account="Liabilities:CreditCard:Tmp:PCF"),
        CanadianTireFinancialImporter(account="Liabilities:CreditCard:Tmp:CTFS"),
        RogersImporter(account="Liabilities:CreditCard:Tmp:Rogers"),
        AmexImporter(account="Liabilities:CreditCard:Tmp:Amex"),
    ]


def _detect_importer(target_file_name: os.PathLike[str]) -> BaseCardImporter:
    importer_id = detect_credit_card_importer_id(Path(target_file_name))
    for candidate in _build_detection_importers():
        if importer_id == "cibc" and isinstance(candidate, CibcImporter):
            return candidate
        if importer_id == "bmo" and isinstance(candidate, BmoImporter):
            return candidate
        if importer_id == "scotia" and isinstance(candidate, ScotiaImporter):
            return candidate
        if importer_id == "rogers" and isinstance(candidate, RogersImporter):
            return candidate
        if importer_id == "mbna" and isinstance(candidate, MbnaImporter):
            return candidate
        if importer_id == "pcf" and isinstance(candidate, PcfImporter):
            return candidate
        if importer_id == "ctfs" and isinstance(candidate, CanadianTireFinancialImporter):
            return candidate
        if importer_id == "amex" and isinstance(candidate, AmexImporter):
            return candidate
    if importer_id:
        raise RuntimeError(f"Unsupported importer id: {importer_id}")
    else:
        raise RuntimeError("Could not determine importer for this CSV.")


def _detect_statement_as_of(importer: BaseCardImporter, target_file_name: os.PathLike[str]) -> datetime.date | None:
    file_memo = _FileMemo(str(target_file_name))
    latest: datetime.date | None = None
    for row in importer.read_rows(file_memo):
        if importer.should_skip(row):
            continue
        try:
            date_str = importer.get_date(row)
            date_format = importer.date_format
            if hasattr(importer, "get_date_format"):
                dynamic_format = importer.get_date_format
                if callable(dynamic_format):
                    date_format = dynamic_format(date_str)
            if date_format is None:
                continue
            txn_date = datetime.datetime.strptime(date_str, date_format).date()
            if latest is None or txn_date > latest:
                latest = txn_date
        except Exception:
            continue
    return latest


def _discover_cibc_accounts(as_of: datetime.date | None, csv_file: str) -> tuple[CibcImporter, str]:
    matches = find_open_accounts(CIBC_ACCOUNT_PATTERNS, as_of=as_of)
    is_simplii_file = "simplii" in csv_file.lower()
    simplii_matches = [account for account in matches if _contains_token(account, "simplii")]
    cibc_matches = [account for account in matches if account not in simplii_matches]

    preferred = simplii_matches if is_simplii_file else cibc_matches
    primary = _select_account(preferred if preferred else matches, account_label="CIBC credit card", as_of=as_of)

    if is_simplii_file:
        account = cibc_matches[0] if cibc_matches else primary
        simplii_account = primary
    else:
        account = primary
        simplii_account = simplii_matches[0] if simplii_matches else primary

    return CibcImporter(account=account, simplii_account=simplii_account), primary


def _discover_bmo_accounts(as_of: datetime.date | None, csv_file: str) -> tuple[BmoImporter, str]:
    matches = find_open_accounts(BMO_ACCOUNT_PATTERNS, as_of=as_of)
    is_porter_file = os.path.basename(csv_file).lower() == "porter.csv"
    porter_matches = [account for account in matches if _contains_token(account, "porter")]
    bmo_matches = [account for account in matches if account not in porter_matches]

    preferred = porter_matches if is_porter_file else bmo_matches
    primary = _select_account(preferred if preferred else matches, account_label="BMO credit card", as_of=as_of)

    porter_account: str | None
    if is_porter_file:
        account = bmo_matches[0] if bmo_matches else primary
        porter_account = primary
    else:
        account = primary
        porter_account = porter_matches[0] if porter_matches else None

    return BmoImporter(account=account, porter_account=porter_account), primary


def _discover_single_account_importer(
    importer_cls: type[BaseCardImporter],
    patterns: list[str],
    *,
    label: str,
    as_of: datetime.date | None,
) -> tuple[BaseCardImporter, str]:
    matches = find_open_accounts(patterns, as_of=as_of)
    selected = _select_account(matches, account_label=label, as_of=as_of)
    return importer_cls(account=selected), selected


def _discover_amex_importer(as_of: datetime.date | None, csv_file: str) -> tuple[AmexImporter, str]:
    matches = find_open_accounts(AMEX_ACCOUNT_PATTERNS, as_of=as_of)
    lower_name = os.path.basename(csv_file).lower()
    keyword_map = {
        "marr": "marriott",
        "gold": "gold",
        "aeroplan": "aeroplan",
        "green": "green",
        "plat": "plat",
    }
    preferred = matches
    for file_token, account_token in keyword_map.items():
        if file_token in lower_name:
            filtered = [account for account in matches if _contains_token(account, account_token)]
            if filtered:
                preferred = filtered
            break

    selected = _select_account(preferred, account_label="AMEX credit card", as_of=as_of)
    return AmexImporter(account=selected), selected


def _resolve_importer(
    target_file_name: os.PathLike[str], csv_file: str
) -> tuple[BaseCardImporter, str, datetime.date | None]:
    detected_importer = _detect_importer(target_file_name)
    as_of = _detect_statement_as_of(detected_importer, target_file_name)

    if isinstance(detected_importer, CibcImporter):
        importer, account = _discover_cibc_accounts(as_of, csv_file)
        return importer, account, as_of
    if isinstance(detected_importer, BmoImporter):
        importer, account = _discover_bmo_accounts(as_of, csv_file)
        return importer, account, as_of
    if isinstance(detected_importer, ScotiaImporter):
        importer, account = _discover_single_account_importer(
            ScotiaImporter,
            SCOTIA_ACCOUNT_PATTERNS,
            label="Scotia credit card",
            as_of=as_of,
        )
        return importer, account, as_of
    if isinstance(detected_importer, RogersImporter):
        importer, account = _discover_single_account_importer(
            RogersImporter,
            ROGERS_ACCOUNT_PATTERNS,
            label="Rogers credit card",
            as_of=as_of,
        )
        return importer, account, as_of
    if isinstance(detected_importer, MbnaImporter):
        importer, account = _discover_single_account_importer(
            MbnaImporter,
            MBNA_ACCOUNT_PATTERNS,
            label="MBNA credit card",
            as_of=as_of,
        )
        return importer, account, as_of
    if isinstance(detected_importer, PcfImporter):
        importer, account = _discover_single_account_importer(
            PcfImporter,
            PCF_ACCOUNT_PATTERNS,
            label="PC Financial credit card",
            as_of=as_of,
        )
        return importer, account, as_of
    if isinstance(detected_importer, CanadianTireFinancialImporter):
        importer, account = _discover_single_account_importer(
            CanadianTireFinancialImporter,
            CTFS_ACCOUNT_PATTERNS,
            label="CTFS credit card",
            as_of=as_of,
        )
        return importer, account, as_of
    if isinstance(detected_importer, AmexImporter):
        importer, account = _discover_amex_importer(as_of, csv_file)
        return importer, account, as_of
    raise RuntimeError(f"Unsupported importer type: {detected_importer.__class__.__name__}")


def main() -> None:
    # Safety check: warn about uncommitted changes
    confirm_uncommitted_changes()

    # Auto-detect CSV file if not provided
    csv_file: str | None
    if len(sys.argv) >= 2:
        csv_file = sys.argv[1]
    else:
        try:
            csv_file = detect_credit_card_csv()
        except RuntimeError as exc:
            logger.error(str(exc))
            sys.exit(1)
        if csv_file is None:
            logger.error("No credit card CSV file found in ~/Downloads")
            logger.info(
                "Supported files: CIBC.csv, statement.csv, report.csv, Transactions.csv, "
                "activity.csv, plat.csv, *AMEX*.csv, SIMPLII*.csv, *Scotiabank*.csv, "
                "Transaction History_*.csv, *MBNA*.csv"
            )
            sys.exit(1)
    assert csv_file is not None

    # Optional manual date override (dates are auto-detected if not provided)
    start_date: str | None = sys.argv[2] if len(sys.argv) >= 4 else None
    end_date: str | None = sys.argv[3] if len(sys.argv) >= 4 else None

    target_file_name = TMPDIR / os.path.basename(csv_file)
    try:
        copy_statement_csv(
            csv_file=csv_file,
            target_path=target_file_name,
            downloads_dir=DOWNLOADED_CSV_BASE_PATH,
            allow_absolute=False,
        )
    except FileNotFoundError:
        logger.error("File not found: %s", csv_file)
        sys.exit(1)

    try:
        importer, card_account, as_of = _resolve_importer(target_file_name, csv_file)
    except RuntimeError as exc:
        logger.error(str(exc))
        sys.exit(1)
    logger.info("Importing for %s", card_account)
    if as_of is not None:
        logger.info("Using account discovery as-of date: %s", as_of.isoformat())

    entries = importer.extract(_FileMemo(str(target_file_name)))
    output_buffer = io.StringIO()
    from beancount.parser import printer

    printer.print_entries(entries, file=output_buffer)
    beancount_output = output_buffer.getvalue()

    if not beancount_output.strip():
        logger.error("Importer produced no output - CSV may be empty or importer may have failed")
        sys.exit(1)

    explicit_dates = start_date is not None and end_date is not None
    start_date, end_date = detect_statement_date_range(
        beancount_output,
        start_date=start_date,
        end_date=end_date,
        include_balance=False,
    )
    if start_date and end_date:
        if not explicit_dates:
            logger.info("Auto-detected date range: %s - %s", start_date, end_date)
    else:
        logger.error("Could not auto-detect dates from transactions")
        # TODO(security): This may include merchant/account/amount details from statements.
        # Keep only for localhost-only operation; redact before non-localhost deployment.
        logger.error("Importer output:\n%s", beancount_output[:500] if beancount_output else "(empty)")
        sys.exit(1)

    result_file_name = build_result_file(card_account, start_date, end_date)
    result_file_path = write_import_output(
        output_content=beancount_output,
        result_file_name=result_file_name,
        records_import_path=BC_RECORD_IMPORT_PATH,
        yearly_summary_path=BC_YEARLY_SUMMARY_PATH,
    )
    logger.info("Result file writing to: %s", result_file_path)
    logger.info("Including %s in yearly summary", result_file_name)


if __name__ == "__main__":
    main()
