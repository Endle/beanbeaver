#!/usr/bin/env python3

import argparse
import sys

from beanbeaver.application.imports.csv_routing import detect_download_route


def main() -> None:
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

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "import":
        if args.import_type is None:
            try:
                route = detect_download_route()
            except RuntimeError as exc:
                print(str(exc))
                sys.exit(1)

            if route is None:
                print("No matching CSV files found in ~/Downloads.")
                print("Expected patterns: credit card or chequing CSVs. Provide a file path or name.")
                sys.exit(1)
            args.import_type = route.import_type
            args.csv_file = route.file_name

        if args.import_type == "cc":
            from beanbeaver.application.imports.credit_card import main as cc_main

            # Pass args to the cc import main
            sys.argv = ["credit_card_import"]
            csv_file = getattr(args, "csv_file", None)
            start_date = getattr(args, "start_date", None)
            end_date = getattr(args, "end_date", None)
            if csv_file:
                sys.argv.append(csv_file)
            if start_date and end_date:
                sys.argv.extend([start_date, end_date])
            cc_main()
        elif args.import_type == "chequing":
            from beanbeaver.application.imports.chequing import main as chequing_main

            # Pass args to the chequing import main
            sys.argv = ["chequing_import"]
            csv_file = getattr(args, "csv_file", None)
            if csv_file:
                sys.argv.append(csv_file)
            chequing_main()

    elif args.command == "scan":
        from beanbeaver.cli.receipt import cmd_scan

        cmd_scan(args)
    elif args.command == "serve":
        from beanbeaver.cli.receipt import cmd_serve

        cmd_serve(args)
    elif args.command == "list-approved":
        from beanbeaver.cli.receipt import cmd_list_approved

        cmd_list_approved(args)
    elif args.command == "list-scanned":
        from beanbeaver.cli.receipt import cmd_list_scanned

        cmd_list_scanned(args)
    elif args.command == "edit":
        from beanbeaver.cli.receipt import cmd_edit

        cmd_edit(args)
    elif args.command == "re-edit":
        from beanbeaver.cli.receipt import cmd_re_edit

        cmd_re_edit(args)
    elif args.command == "match":
        from beanbeaver.application.receipts.match import cmd_match

        cmd_match(args)


if __name__ == "__main__":
    main()
