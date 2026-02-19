#!/usr/bin/env python3

import argparse
from collections.abc import Callable, Sequence

from beanbeaver.application.imports.csv_routing import detect_download_route


def _coerce_exit_code(code: object) -> int:
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


def _run_legacy_command(command: Callable[[argparse.Namespace], None], args: argparse.Namespace) -> int:
    """
    Normalize legacy command handlers that still call sys.exit().

    This keeps process termination centralized in this module's entrypoint.
    """
    try:
        command(args)
    except SystemExit as exc:
        return _coerce_exit_code(exc.code)
    return 0


def _print_error(error: str) -> None:
    for line in error.splitlines():
        print(line)


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        description="Beancount utilities CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  import [cc|chequing] [csv_file]
                             Import transactions (auto-detect type if omitted)
  scan <image>               Scan a receipt image
  serve [--port]             Start receipt upload server
  list-approved              List approved receipts
  list-scanned               List scanned receipts
  edit                       Edit a scanned receipt (interactive)
  re-edit                    Re-edit an approved receipt (interactive)
  match [ledger]             Match approved receipts against ledger

Notes:
  receipts/scanned/  = OCR+parser succeeded, not reviewed
  receipts/approved/ = human reviewed and edited
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Import subcommand
    import_parser = subparsers.add_parser("import", help="Import transactions")
    import_subparsers = import_parser.add_subparsers(dest="import_type", help="Import type")

    # import cc
    cc_parser = import_subparsers.add_parser("cc", help="Import credit card transactions")
    cc_parser.add_argument("csv_file", nargs="?", help="CSV file to import (auto-detect if not provided)")
    cc_parser.add_argument("start_date", nargs="?", help="Start date (MMDD format, auto-detect if not provided)")
    cc_parser.add_argument("end_date", nargs="?", help="End date (MMDD format, auto-detect if not provided)")

    # import chequing
    chequing_parser = import_subparsers.add_parser("chequing", help="Import chequing transactions")
    chequing_parser.add_argument("csv_file", nargs="?", help="CSV file to import (auto-detect if not provided)")

    # scan command
    scan_parser = subparsers.add_parser("scan", help="Scan a receipt image")
    scan_parser.add_argument("image", help="Path to receipt image")
    scan_parser.add_argument(
        "--ocr-url", default="http://localhost:8001", help="OCR service URL (default: http://localhost:8001)"
    )
    scan_parser.add_argument("--no-edit", action="store_true", help="Skip editor and leave draft in receipts/scanned/")
    # serve command
    serve_parser = subparsers.add_parser("serve", help="Start receipt upload server")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=8080, help="Port to bind to (default: 8080)")

    # list commands
    subparsers.add_parser("list-approved", help="List approved receipts")
    subparsers.add_parser("list-scanned", help="List scanned receipts")

    # edit (interactive editor for scanned receipts)
    subparsers.add_parser("edit", help="Edit a scanned receipt (interactive)")
    subparsers.add_parser("re-edit", help="Re-edit an approved receipt (interactive)")

    # match approved receipts against ledger
    match_parser = subparsers.add_parser("match", help="Match approved receipts against ledger")
    match_parser.add_argument(
        "ledger",
        nargs="?",
        default=None,
        help="Path to beancount ledger file (default: main.beancount)",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "import":
        if args.import_type is None:
            try:
                route = detect_download_route()
            except RuntimeError as exc:
                print(str(exc))
                return 1

            if route is None:
                print("No matching CSV files found in ~/Downloads.")
                print("Expected patterns: credit card or chequing CSVs. Provide a file path or name.")
                return 1
            args.import_type = route.import_type
            args.csv_file = route.file_name

        if args.import_type == "cc":
            from beanbeaver.application.imports.credit_card import CreditCardImportRequest, run_credit_card_import

            cc_result = run_credit_card_import(
                CreditCardImportRequest(
                    csv_file=getattr(args, "csv_file", None),
                    start_date=getattr(args, "start_date", None),
                    end_date=getattr(args, "end_date", None),
                )
            )
            if cc_result.status == "error":
                assert cc_result.error is not None
                _print_error(cc_result.error)
                return 1
            return 0

        if args.import_type == "chequing":
            from beanbeaver.application.imports.chequing import ChequingImportRequest, run_chequing_import

            chequing_result = run_chequing_import(
                ChequingImportRequest(
                    csv_file=getattr(args, "csv_file", None),
                )
            )
            if chequing_result.status == "error":
                assert chequing_result.error is not None
                _print_error(chequing_result.error)
                return 1
            return 0

        print(f"Unsupported import type: {args.import_type}")
        return 1

    elif args.command == "scan":
        from beanbeaver.cli.receipt import cmd_scan

        return _run_legacy_command(cmd_scan, args)
    elif args.command == "serve":
        from beanbeaver.cli.receipt import cmd_serve

        return _run_legacy_command(cmd_serve, args)
    elif args.command == "list-approved":
        from beanbeaver.cli.receipt import cmd_list_approved

        return _run_legacy_command(cmd_list_approved, args)
    elif args.command == "list-scanned":
        from beanbeaver.cli.receipt import cmd_list_scanned

        return _run_legacy_command(cmd_list_scanned, args)
    elif args.command == "edit":
        from beanbeaver.cli.receipt import cmd_edit

        return _run_legacy_command(cmd_edit, args)
    elif args.command == "re-edit":
        from beanbeaver.cli.receipt import cmd_re_edit

        return _run_legacy_command(cmd_re_edit, args)

    if args.command == "match":
        from beanbeaver.application.receipts.match import cmd_match

        return _run_legacy_command(cmd_match, args)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
