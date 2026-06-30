#!/usr/bin/env python3

import argparse
from collections.abc import Callable, Sequence


def _coerce_exit_code(code: object) -> int:
    """Normalize a SystemExit code (None/int/other) to a process exit int."""
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
    """Print a multi-line error message line by line."""
    for line in error.splitlines():
        print(line)


def _dispatch_import_command(args: argparse.Namespace) -> int:
    """Run the `import` subcommand (auto-detecting the type when omitted)."""
    from beanbeaver.application.imports.csv_routing import detect_download_route
    from beanbeaver.application.imports.shared import downloads_display_path

    if args.import_type is None:
        try:
            route = detect_download_route()
        except RuntimeError as exc:
            print(str(exc))
            return 1

        if route is None:
            print(f"No matching CSV files found in {downloads_display_path()}.")
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


def _dispatch_api_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Route an `api <command>` to its handler via a name→handler table."""
    from beanbeaver.cli import api as cli_api

    handlers: dict[str, Callable[[argparse.Namespace], None]] = {
        "list-scanned": cli_api.cmd_api_list_scanned,
        "list-approved": cli_api.cmd_api_list_approved,
        "list-item-categories": cli_api.cmd_api_list_item_categories,
        "show-receipt": cli_api.cmd_api_show_receipt,
        "approve-scanned": cli_api.cmd_api_approve_scanned,
        "approve-scanned-with-review": cli_api.cmd_api_approve_scanned_with_review,
        "re-edit-approved-with-review": cli_api.cmd_api_re_edit_approved_with_review,
        "match-candidates": cli_api.cmd_api_match_candidates,
        "apply-match": cli_api.cmd_api_apply_match,
        "plan-import": cli_api.cmd_api_plan_import,
        "refresh-import-page": cli_api.cmd_api_refresh_import_page,
        "resolve-import-accounts": cli_api.cmd_api_resolve_import_accounts,
        "apply-import": cli_api.cmd_api_apply_import,
        "import-apply": cli_api.cmd_api_import_apply,
        "preflight-chequing-import": cli_api.cmd_api_preflight_chequing_import,
        "preflight-cc-import": cli_api.cmd_api_preflight_cc_import,
        "get-config": cli_api.cmd_api_get_config,
        "set-config": cli_api.cmd_api_set_config,
    }
    handler = handlers.get(args.api_command)
    if handler is None:
        parser.print_help()
        return 1
    return _run_legacy_command(handler, args)


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        description="Beancount utilities CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  import [cc|chequing] [csv_file]
                             Import transactions (auto-detect type if omitted)
  serve [--port]             Start receipt upload server
  list-approved              List approved receipts
  list-scanned               List scanned receipts
  edit                       Edit a scanned receipt (interactive)
  re-edit                    Re-edit an approved receipt (interactive)
  match [ledger]             Match approved receipts against ledger
  fetch-models [--set]       Download native-OCR model weights (OCR_BACKEND=native)

Notes:
  receipts/<receipt-dir>/stages/000_parsed.receipt.json = OCR+parser succeeded, not reviewed
  receipts/<receipt-dir>/stages/010_review.receipt.json = human reviewed and edited
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

    # serve command
    serve_parser = subparsers.add_parser("serve", help="Start receipt upload server")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=8080, help="Port to bind to (default: 8080)")

    # list commands
    subparsers.add_parser("list-approved", help="List approved receipts")
    subparsers.add_parser("list-scanned", help="List scanned receipts")

    # edit (interactive editor for scanned receipts)
    subparsers.add_parser("edit", help="Edit a scanned receipt (interactive)")
    reedit_parser = subparsers.add_parser("re-edit", help="Re-edit an approved receipt (interactive)")
    reedit_parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Path to an approved staged receipt JSON file (skip interactive selection if provided)",
    )

    # match approved receipts against ledger
    match_parser = subparsers.add_parser("match", help="Match approved receipts against ledger")
    match_parser.add_argument(
        "ledger",
        nargs="?",
        default=None,
        help="Path to beancount ledger file (default: main.beancount)",
    )

    # fetch-models (download native-OCR model weights into the per-user cache)
    fetch_models_parser = subparsers.add_parser(
        "fetch-models",
        help="Download native-OCR model weights (for OCR_BACKEND=native)",
    )
    fetch_models_parser.add_argument(
        "--set",
        dest="model_set",
        choices=["server", "mobile", "both"],
        default="server",
        help="Model set to download (default: server, 88 MB matching-grade detector)",
    )
    fetch_models_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if a verified copy already exists",
    )

    api_parser = subparsers.add_parser("api", help="Machine-readable backend commands")
    api_subparsers = api_parser.add_subparsers(dest="api_command", help="API commands")

    api_subparsers.add_parser("list-scanned", help="List scanned receipts as JSON")
    api_subparsers.add_parser("list-approved", help="List approved receipts as JSON")
    api_subparsers.add_parser("list-item-categories", help="List available item categories as JSON")
    api_subparsers.add_parser("get-config", help="Get TUI/backend config as JSON")
    api_subparsers.add_parser("set-config", help="Persist TUI/backend config from stdin JSON")
    api_subparsers.add_parser("plan-import", help="Plan statement import from stdin JSON")
    api_subparsers.add_parser("refresh-import-page", help="Refresh the Imports page from stdin JSON")
    api_subparsers.add_parser("resolve-import-accounts", help="List candidate import accounts from stdin JSON")
    api_subparsers.add_parser("apply-import", help="Apply one statement import from stdin JSON")
    api_subparsers.add_parser("import-apply", help="Apply one statement import with a JSON-only response")
    api_subparsers.add_parser(
        "preflight-chequing-import",
        help="Preflight a chequing CSV and surface ambiguous account decisions as JSON",
    )
    api_subparsers.add_parser(
        "preflight-cc-import",
        help="Preflight a credit-card CSV and surface per-transaction categories as JSON",
    )

    show_receipt_parser = api_subparsers.add_parser("show-receipt", help="Show one staged receipt document as JSON")
    show_receipt_parser.add_argument("path", help="Path to a staged receipt JSON file")

    approve_scanned_parser = api_subparsers.add_parser(
        "approve-scanned",
        help="Approve one scanned receipt without interactive editing",
    )
    approve_scanned_parser.add_argument("path", help="Path to a staged receipt JSON file in scanned/")

    approve_scanned_review_parser = api_subparsers.add_parser(
        "approve-scanned-with-review",
        help="Approve one scanned receipt with receipt-level review overrides from stdin JSON",
    )
    approve_scanned_review_parser.add_argument("path", help="Path to a staged receipt JSON file in scanned/")
    reedit_approved_review_parser = api_subparsers.add_parser(
        "re-edit-approved-with-review",
        help="Update one approved receipt with receipt-level review overrides from stdin JSON",
    )
    reedit_approved_review_parser.add_argument("path", help="Path to a staged receipt JSON file in approved/")
    match_candidates_parser = api_subparsers.add_parser(
        "match-candidates",
        help="List candidate ledger matches for one approved receipt",
    )
    match_candidates_parser.add_argument("path", help="Path to a staged receipt JSON file in approved/")
    apply_match_parser = api_subparsers.add_parser(
        "apply-match",
        help="Apply one selected ledger match for an approved receipt using stdin JSON",
    )
    apply_match_parser.add_argument("path", help="Path to a staged receipt JSON file in approved/")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "import":
        return _dispatch_import_command(args)

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
    elif args.command == "fetch-models":
        from beanbeaver.cli.receipt import cmd_fetch_models

        return _run_legacy_command(cmd_fetch_models, args)
    elif args.command == "api":
        return _dispatch_api_command(args, parser)

    if args.command == "match":
        from beanbeaver.application.receipts.match import cmd_match
        from beanbeaver.cli.receipt import _resolve_editor

        args.resolve_editor_cmd = _resolve_editor
        return _run_legacy_command(cmd_match, args)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
