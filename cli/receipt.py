"""Receipt command handlers used by the unified CLI."""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from beanbeaver.runtime import get_logger

logger = get_logger(__name__)


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the FastAPI server for receiving receipt uploads."""
    import uvicorn

    from beanbeaver.runtime import receipt_server as server

    print(f"Starting receipt server on {args.host}:{args.port}")
    print(f"Upload endpoints: http://{args.host}:{args.port}/upload | /beanbeaver | /bb")
    print("Press Ctrl+C to stop")

    uvicorn.run(server.app, host=args.host, port=args.port)


def cmd_scan(args: argparse.Namespace) -> None:
    """Scan a receipt image, allow manual edit, then stage to approved/."""
    # TODO: add CLI tests that cover cmd_scan status handling and exit codes.
    from beanbeaver.application.receipts.scan import ReceiptScanRequest, run_receipt_scan

    receipt_path = Path(args.image)
    result = run_receipt_scan(
        ReceiptScanRequest(
            image_path=receipt_path,
            ocr_url=args.ocr_url,
            no_edit=args.no_edit,
            resolve_editor_cmd=_resolve_editor,
        )
    )

    if result.status == "file_not_found":
        logger.error("%s", result.error)
        print(f"Error: {result.error}")
        sys.exit(1)

    if result.status == "ocr_unavailable":
        logger.error("%s", result.error)
        print(f"OCR service unavailable: {result.error}")
        print("Make sure the OCR service is running before scanning receipts.")
        sys.exit(1)

    receipt = result.receipt
    if receipt is None or result.scanned_path is None:
        print("Scan failed: missing receipt output.")
        sys.exit(1)

    # Display parsed items for review
    print("\n" + "=" * 60)
    print("PARSED RECEIPT")
    print("=" * 60)
    print(f"Merchant: {receipt.merchant}")
    date_str = receipt.date.isoformat() if not receipt.date_is_placeholder else "UNKNOWN"
    print(f"Date: {date_str}")
    print(f"Total: ${receipt.total:.2f}")
    if receipt.tax:
        print(f"Tax: ${receipt.tax:.2f}")
    print(f"\nItems ({len(receipt.items)}):")
    for i, item in enumerate(receipt.items, 1):
        qty_str = f" x{item.quantity}" if item.quantity > 1 else ""
        cat_str = f" [{item.category}]" if item.category else ""
        print(f"  {i}. {item.description}{qty_str} - ${item.price:.2f}{cat_str}")
    print("=" * 60)

    print(f"\nSaved draft to: {result.scanned_path}")

    if result.status == "scanned_saved":
        print("Draft left in scanned/ (edit manually, then move to approved/).")
        return

    if result.status == "editor_not_found":
        editor_cmd = result.editor_cmd or []
        print(f"Editor not found: {' '.join(editor_cmd)}")
        print("Draft left in scanned/ (edit manually, then move to approved/).")
        return

    if result.status == "editor_failed":
        print(f"Editor exited with code {result.editor_returncode}. Draft left in scanned/.")
        return

    if result.status == "approved_staged" and result.approved_path is not None:
        print(f"\nStaged to approved/: {result.approved_path}")
    else:
        print("Draft left in scanned/ (edit manually, then move to approved/).")
        return
    print("This receipt is ready for matching (bb match or CC import).")


def _resolve_editor() -> list[str]:
    """Resolve editor command: git core.editor -> $EDITOR -> vi."""
    git_path = shutil.which("git")
    if git_path:
        try:
            result = subprocess.run(
                ["git", "config", "--global", "core.editor"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                editor = result.stdout.strip()
                if editor:
                    # Reject shell operators; fall back to $EDITOR for complex commands.
                    if any(ch in editor for ch in ["|", "&", ";", "<", ">", "`", "$", "(", ")"]):
                        print("Unsupported git core.editor (shell operators). Falling back to $EDITOR.")
                    else:
                        return shlex.split(editor)
        except Exception:
            pass

    env_editor = os.environ.get("EDITOR", "").strip()
    if env_editor:
        return shlex.split(env_editor)

    return ["vi"]


def cmd_edit(args: argparse.Namespace) -> None:
    """Interactively edit a scanned receipt and stage to approved/."""
    from beanbeaver.application.receipts.review import EditScannedReceiptRequest, run_edit_scanned_receipt
    from beanbeaver.runtime.receipt_storage import list_scanned_receipts

    if not sys.stdin.isatty():
        print("Error: bb edit requires an interactive TTY.")
        sys.exit(1)

    receipts = list_scanned_receipts()
    if not receipts:
        print("No scanned receipts found in receipts/scanned/")
        return

    print("\nScanned receipts:")
    for i, path in enumerate(receipts, 1):
        print(f"{i}. {path.name}")
    print("q. Quit")

    choice = input("Select a receipt to edit: ").strip().lower()
    if choice in {"q", "quit", ""}:
        print("Cancelled.")
        return

    try:
        idx = int(choice)
        if idx < 1 or idx > len(receipts):
            raise ValueError
    except ValueError:
        print("Invalid choice.")
        return

    target = receipts[idx - 1]
    result = run_edit_scanned_receipt(
        EditScannedReceiptRequest(
            target_path=target,
            resolve_editor_cmd=_resolve_editor,
        )
    )
    if result.status == "editor_not_found":
        editor_cmd = result.editor_cmd or []
        print(f"Editor not found: {' '.join(editor_cmd)}")
        return
    if result.status == "editor_failed":
        print(f"Editor exited with code {result.editor_returncode}. Draft left in scanned/.")
        return
    if result.status == "edited_file_missing":
        print("Edited file no longer exists. Leaving as-is.")
        return

    if result.approved_path is None:
        print("Edited receipt was not staged. Leaving as-is.")
        return

    approved_path = result.approved_path
    print(f"Staged to approved/: {approved_path}")


def cmd_re_edit(args: argparse.Namespace) -> None:
    """Interactively re-edit an approved receipt in-place."""
    from beanbeaver.application.receipts.review import (
        ReEditApprovedReceiptRequest,
        run_re_edit_approved_receipt,
    )
    from beanbeaver.runtime.receipt_storage import list_approved_receipts

    if not sys.stdin.isatty():
        print("Error: bb re-edit requires an interactive TTY.")
        sys.exit(1)

    receipts = list_approved_receipts()
    if not receipts:
        print("No approved receipts found in receipts/approved/")
        return

    print("\nApproved receipts:")
    for i, (path, merchant, receipt_date, amount) in enumerate(receipts, 1):
        date_str = receipt_date.isoformat() if receipt_date else "UNKNOWN"
        print(f"{i}. {date_str}  ${amount:>7.2f}  {merchant:<30}  {path.name}")
    print("q. Quit")

    choice = input("Select a receipt to re-edit: ").strip().lower()
    if choice in {"q", "quit", ""}:
        print("Cancelled.")
        return

    try:
        idx = int(choice)
        if idx < 1 or idx > len(receipts):
            raise ValueError
    except ValueError:
        print("Invalid choice.")
        return

    target = receipts[idx - 1][0]
    result = run_re_edit_approved_receipt(
        ReEditApprovedReceiptRequest(
            target_path=target,
            resolve_editor_cmd=_resolve_editor,
        )
    )
    if result.status == "editor_not_found":
        editor_cmd = result.editor_cmd or []
        print(f"Editor not found: {' '.join(editor_cmd)}")
        return
    if result.status == "editor_failed":
        print(f"Editor exited with code {result.editor_returncode}. Approved file left unchanged.")
        return
    if result.status == "edited_file_missing":
        print("Edited file no longer exists. Leaving as-is.")
        return
    if result.status == "normalize_failed":
        print(f"Re-edit saved, but could not normalize filename: {result.normalize_error}")
        return

    if result.updated_path is None:
        print("Approved receipt update failed.")
        return

    print(f"Updated approved receipt: {result.updated_path}")


def cmd_list_approved(args: argparse.Namespace) -> None:
    """List approved receipts in approved/ directory."""
    from beanbeaver.application.receipts.listing import run_list_approved_receipts

    receipts = run_list_approved_receipts().receipts

    if not receipts:
        print("No approved receipts found in receipts/approved/")
        return

    print(f"\nApproved receipts ({len(receipts)}):")
    print("-" * 60)
    for path, merchant, receipt_date, amount in receipts:
        date_str = receipt_date.isoformat() if receipt_date else "UNKNOWN"
        print(f"  {date_str}  ${amount:>7.2f}  {merchant:<30}  {path.name}")
    print("-" * 60)
    print(f"Total: {len(receipts)} receipt(s) awaiting CC match")


def cmd_list_scanned(args: argparse.Namespace) -> None:
    """List scanned receipts in scanned/ directory."""
    from beanbeaver.application.receipts.listing import run_list_scanned_receipts

    receipts = run_list_scanned_receipts().receipts

    if not receipts:
        print("No scanned receipts found in receipts/scanned/")
        return

    print(f"\nScanned receipts ({len(receipts)}):")
    print("-" * 60)
    for path in receipts:
        print(f"  {path.name}")
    print("-" * 60)
    print(f"Total: {len(receipts)} receipt(s) awaiting manual review")


def cmd_debug_overlay(args: argparse.Namespace) -> None:
    """Create debug image with OCR bounding boxes overlay."""
    from beanbeaver.runtime.receipt_pipeline import create_debug_overlay_from_json

    image_path = Path(args.image)
    if not image_path.exists():
        logger.error("Image file not found: %s", image_path)
        print(f"Error: Image file not found: {image_path}")
        sys.exit(1)

    json_path = Path(args.json_path) if args.json_path else None
    output_path = Path(args.output) if args.output else None

    try:
        result_path = create_debug_overlay_from_json(image_path, json_path)
        if output_path and result_path != output_path:
            import shutil

            shutil.move(result_path, output_path)
            result_path = output_path
        print(f"Debug overlay created: {result_path}")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Run 'receipt scan' first to generate the OCR JSON, or specify --json path")
        sys.exit(1)


def main() -> int:
    """Compatibility entrypoint; delegates to the unified CLI parser."""
    from beanbeaver.cli.main import main as unified_main

    return unified_main()


if __name__ == "__main__":
    raise SystemExit(main())
