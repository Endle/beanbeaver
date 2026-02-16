"""Match approved receipts against ledger transactions."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

from beanbeaver.domain.match import (
    itemized_receipt_total,
    match_key,
    transaction_charge_amount,
)
from beanbeaver.ledger_access import get_ledger_writer
from beanbeaver.runtime import get_logger, get_paths

logger = get_logger(__name__)

type ReceiptSummary = tuple[Path, str, date, Decimal]


def _ensure_git_clean_before_match() -> bool:
    """Check git worktree cleanliness before matching with interactive guardrails."""
    if shutil.which("git") is None:
        print("Warning: git not found; skipping clean-worktree check.")
        return True

    while True:
        repo = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if repo.returncode != 0:
            print("Warning: not in a git repository; skipping clean-worktree check.")
            return True

        repo_root = repo.stdout.strip()
        status = subprocess.run(
            ["git", "-C", repo_root, "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
        if status.returncode != 0:
            print("Warning: failed to read git status; skipping clean-worktree check.")
            return True

        dirty_lines = [line for line in status.stdout.splitlines() if line.strip()]
        if not dirty_lines:
            return True

        print("\nWorking tree is not clean:")
        for line in dirty_lines[:20]:
            print(f"  {line}")
        if len(dirty_lines) > 20:
            print(f"  ... and {len(dirty_lines) - 20} more")

        print("\nBefore matching, choose:")
        print("  1. Force continue")
        print("  2. Check again")
        print("  3. Quit")
        choice = input("Select [3]: ").strip()
        if choice == "1":
            return True
        if choice == "2":
            continue
        if choice in {"", "3", "q", "quit"}:
            print("Cancelled.")
            return False
        print("Invalid choice. Enter 1, 2, or 3.")


def _select_receipts_for_match(
    pending: Sequence[ReceiptSummary],
) -> list[ReceiptSummary] | None:
    """Let user select one approved receipt or all receipts for matching."""
    print(f"\nApproved receipts ({len(pending)}):")
    print("-" * 80)
    for i, (path, merchant, receipt_date, amount) in enumerate(pending, 1):
        date_str = receipt_date.isoformat() if receipt_date else "UNKNOWN"
        print(f"{i:>3}. {date_str}  ${amount:>7.2f}  {merchant:<28}  {path.name}")
    print("-" * 80)
    print("a. Match all approved receipts")
    print("q. Quit")

    while True:
        choice = input("Select receipt to match [q]: ").strip().lower()
        if choice in {"", "q", "quit"}:
            print("Cancelled.")
            return None
        if choice == "a":
            return list(pending)
        try:
            idx = int(choice)
            if 1 <= idx <= len(pending):
                return [pending[idx - 1]]
        except ValueError:
            pass
        print("Invalid selection. Enter a number, 'a', or 'q'.")


def cmd_match(args: argparse.Namespace) -> None:
    """Match all approved receipts against ledger."""
    from beanbeaver.ledger_access import get_ledger_reader
    from beanbeaver.receipt.formatter import format_enriched_transaction
    from beanbeaver.receipt.matcher import format_match_for_display, match_receipt_to_transactions
    from beanbeaver.runtime.receipt_storage import (
        delete_receipt,
        list_approved_receipts,
        list_scanned_receipts,
        move_to_matched,
        parse_receipt_from_beancount,
    )
    from beancount.core import data as beancount_data

    if not sys.stdin.isatty():
        print("Error: bb match requires an interactive TTY.")
        sys.exit(1)

    if not _ensure_git_clean_before_match():
        return

    scanned = list_scanned_receipts()
    if scanned:
        print(
            f"Warning: {len(scanned)} receipt(s) still in receipts/scanned/. "
            "Review with `bb edit` to move them to approved."
        )

    ledger_arg = getattr(args, "ledger", None)
    ledger_path = Path(ledger_arg) if ledger_arg else get_paths().main_beancount
    if not ledger_path.exists():
        logger.error("Ledger file not found: %s", ledger_path)
        print(f"Error: Ledger file not found: {ledger_path}")
        sys.exit(1)

    print(f"Loading ledger from {ledger_path}...")
    ledger_reader = get_ledger_reader()
    loaded = ledger_reader.load(ledger_path=ledger_path)
    if loaded.errors:
        logger.warning("Loaded ledger with %d beancount error(s); matching may be unreliable.", len(loaded.errors))
    transactions = [e for e in loaded.entries if isinstance(e, beancount_data.Transaction)]
    print(f"Loaded {len(transactions)} transactions")

    pending = list_approved_receipts()
    if not pending:
        print("No approved receipts to match.")
        return

    selected_receipts = _select_receipts_for_match(pending)
    if not selected_receipts:
        return

    print(f"\nMatching {len(selected_receipts)} approved receipt(s)...")
    print("=" * 60)

    matched_count = 0
    skipped_count = 0
    used_matches: set[tuple[str, int]] = set()
    stopped_early = False
    ledger_writer = get_ledger_writer()

    for path, merchant, receipt_date, amount in selected_receipts:
        date_str = receipt_date.isoformat() if receipt_date else "UNKNOWN"
        print(f"\n{path.name}")
        print(f"  {merchant} | {date_str} | ${amount:.2f}")

        receipt = parse_receipt_from_beancount(path)
        matches = match_receipt_to_transactions(receipt, transactions)
        available_matches = [m for m in matches if match_key(m) not in used_matches]

        if not available_matches and matches:
            print("  All candidates were already used in this run.")
            while True:
                reuse_choice = input("  [u] Show used candidates | [s] Skip | [q] Quit: ").strip().lower()
                if reuse_choice in {"s", "skip"}:
                    print("  Skipped")
                    skipped_count += 1
                    break
                if reuse_choice in {"q", "quit"}:
                    print("Stopping matching session.")
                    stopped_early = True
                    break
                if reuse_choice in {"u", "use"}:
                    available_matches = matches
                    break
                print("  Invalid choice. Enter u, s, or q.")
            if stopped_early:
                break
            if not available_matches:
                continue

        if not matches:
            print("  No matches found - keeping in approved")
            skipped_count += 1
            continue

        print(f"  Found {len(matches)} match(es), {len(available_matches)} available:")
        display_matches = available_matches[:5]
        for i, match in enumerate(display_matches, 1):
            already_used = " (already used)" if match_key(match) in used_matches else ""
            formatted = format_match_for_display(match).strip().replace(chr(10), chr(10) + "        ")
            print(f"    [{i}] {formatted}{already_used}")

        valid_choices = [str(i) for i in range(1, len(display_matches) + 1)] + ["s", "d", "q"]
        print("    [s] Skip | [d] Delete receipt | [q] Quit")

        while True:
            choice = input("  Select: ").strip().lower()
            if choice in valid_choices:
                break
            print(f"    Invalid. Enter one of: {', '.join(valid_choices)}")

        if choice == "d":
            delete_receipt(path)
            print("  Deleted")
        elif choice == "s":
            print("  Skipped")
            skipped_count += 1
        elif choice == "q":
            print("Stopping matching session.")
            stopped_early = True
            break
        else:
            selected_idx = int(choice) - 1
            selected_match = display_matches[selected_idx]
            key = match_key(selected_match)
            if key in used_matches:
                confirm = input("  Candidate already used earlier. Reuse it? [y/N]: ").strip().lower()
                if confirm not in {"y", "yes"}:
                    print("  Skipped")
                    skipped_count += 1
                    continue

            matched_file = Path(selected_match.file_path)
            if str(matched_file) == "unknown" or not matched_file.exists():
                print(f"  Match target file missing: {selected_match.file_path}")
                skipped_count += 1
                continue

            expected_total = transaction_charge_amount(selected_match)
            itemized_total = itemized_receipt_total(receipt)
            if expected_total is not None:
                delta = expected_total - itemized_total
                if delta < Decimal("-0.01"):
                    print(
                        "  Failed to apply match: itemized receipt total "
                        f"(${itemized_total:.2f}) exceeds card transaction (${expected_total:.2f}) "
                        f"by ${abs(delta):.2f}. Re-edit receipt first."
                    )
                    skipped_count += 1
                    continue

            enriched = format_enriched_transaction(receipt, selected_match)
            enriched_dir = matched_file.parent / "_enriched"
            enriched_dir.mkdir(parents=True, exist_ok=True)
            enriched_path = enriched_dir / f"{path.stem}-enriched.beancount"
            include_rel = enriched_path.relative_to(matched_file.parent).as_posix()

            try:
                status = ledger_writer.apply_receipt_match(
                    ledger_path=ledger_path,
                    statement_path=matched_file,
                    line_number=selected_match.line_number,
                    include_rel_path=include_rel,
                    receipt_name=path.name,
                    enriched_path=enriched_path,
                    enriched_content=enriched,
                )

                move_to_matched(path)
                action_msg = "already applied; receipt archived" if status == "already_applied" else "applied"
                print(f"  Matched! Transaction {action_msg}. Enriched file: {enriched_path}")
                matched_count += 1
                used_matches.add(key)

                # Reload transactions so next matches use updated line numbers/content.
                reloaded = ledger_reader.load(ledger_path=ledger_path)
                if reloaded.errors:
                    print("  Warning: ledger reload has errors; stopping session.")
                    stopped_early = True
                    break
                transactions = [e for e in reloaded.entries if isinstance(e, beancount_data.Transaction)]
            except Exception as exc:
                print(f"  Failed to apply match: {exc}")
                skipped_count += 1

    print("\n" + "=" * 60)
    if stopped_early:
        print("Stopped early by user.")
    print(f"Done. Matched: {matched_count}, Skipped: {skipped_count}")
