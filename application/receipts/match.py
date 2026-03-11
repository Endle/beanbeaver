"""Match approved receipts against ledger transactions."""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from beanbeaver.domain.match import (
    itemized_receipt_total,
    match_key,
    transaction_charge_amount,
)
from beanbeaver.domain.receipt import Receipt
from beanbeaver.ledger_access import (
    ReceiptMatchFileSnapshot,
    apply_receipt_match,
    list_transactions,
    open_accounts,
    restore_receipt_match_files,
    snapshot_receipt_match_files,
)
from beanbeaver.runtime import get_logger, get_paths, load_merchant_families

logger = get_logger(__name__)

type ReceiptSummary = tuple[Path, str | None, date | None, Decimal | None]
type ResolveEditorCmd = Callable[[], list[str]]


def _receipt_chain_name(stage_path: Path) -> str:
    """Return a stable human-readable name for one receipt chain."""
    return stage_path.parent.name


@dataclass(frozen=True)
class MatchCandidate:
    """One candidate ledger transaction for an approved receipt."""

    file_path: str
    line_number: int
    confidence: float
    display: str
    payee: str | None
    narration: str | None
    date: date
    amount: Decimal | None


@dataclass(frozen=True)
class MatchCandidatesResult:
    """Candidate transactions for one approved receipt."""

    ledger_path: Path
    candidates: list[MatchCandidate]
    errors: list[str]
    warning: str | None = None


@dataclass(frozen=True)
class _ResolvedMatchCandidates:
    """Resolved candidate set after strict match and optional fallback."""

    matches: list[Any]
    used_relaxed_threshold: bool
    warning: str | None


@dataclass(frozen=True)
class ApplyMatchResult:
    """Outcome of applying one receipt-to-ledger match."""

    status: str
    ledger_path: Path
    matched_receipt_path: Path | None = None
    enriched_path: Path | None = None
    message: str | None = None


@dataclass(frozen=True)
class _AppliedMatchUndo:
    """State needed to rollback one successfully applied match."""

    approved_receipt_path: Path
    matched_receipt_path: Path
    ledger_snapshot: ReceiptMatchFileSnapshot


def _restore_receipt_to_approved(
    matched_receipt_path: Path,
    approved_receipt_path: Path,
) -> Path:
    """Move a matched receipt back to approved, handling name collisions."""
    approved_receipt_path.parent.mkdir(parents=True, exist_ok=True)
    target = approved_receipt_path
    if target.exists():
        counter = 1
        base_name = target.stem
        suffix = target.suffix
        while target.exists():
            target = target.with_name(f"{base_name}_{counter}{suffix}")
            counter += 1

    matched_receipt_path.rename(target)
    return target


def _rollback_applied_matches(applied: Sequence[_AppliedMatchUndo]) -> tuple[int, list[str]]:
    """Rollback applied matches in reverse order. Returns (reverted_count, warnings)."""
    reverted_count = 0
    warnings: list[str] = []

    for undo in reversed(applied):
        try:
            restore_receipt_match_files(undo.ledger_snapshot)

            if undo.matched_receipt_path.exists():
                _restore_receipt_to_approved(
                    matched_receipt_path=undo.matched_receipt_path,
                    approved_receipt_path=undo.approved_receipt_path,
                )
            else:
                warnings.append(f"Matched receipt not found during rollback: {undo.matched_receipt_path}")
            reverted_count += 1
        except Exception as exc:
            warnings.append(f"Rollback failed for {undo.approved_receipt_path.name}: {exc}")

    return reverted_count, warnings


def _format_ledger_errors(errors: Sequence[Any], *, limit: int = 5) -> list[str]:
    """Render a concise list of Beancount loader errors for CLI output."""
    formatted: list[str] = []
    for err in list(errors)[:limit]:
        source = getattr(err, "source", None)
        message = getattr(err, "message", None) or str(err)
        if isinstance(source, dict):
            filename = source.get("filename")
            lineno = source.get("lineno")
            if filename and lineno:
                formatted.append(f"{filename}:{lineno} - {message}")
                continue
            if filename:
                formatted.append(f"{filename} - {message}")
                continue
        formatted.append(str(message))
    return formatted


def _load_ledger_transactions(ledger_path: Path) -> tuple[object, list[str]]:
    """Load ledger transactions and convert diagnostics to CLI/API-safe strings."""
    snapshot = list_transactions(ledger_path=ledger_path)
    errors = _format_ledger_errors(snapshot.errors, limit=5)
    return snapshot, errors


def _resolve_receipt_match_candidates(
    receipt: Receipt,
    transactions: Sequence[object],
    *,
    merchant_families: Sequence[object] | None,
) -> _ResolvedMatchCandidates:
    """Return strict matches, or weaker manual-review candidates when strict yields none."""
    from beanbeaver.receipt.matcher import (
        match_receipt_to_transactions,
        relaxed_candidate_match_config,
    )

    strict_matches = match_receipt_to_transactions(
        receipt,
        transactions,
        merchant_families=merchant_families,
    )
    if strict_matches:
        return _ResolvedMatchCandidates(
            matches=list(strict_matches),
            used_relaxed_threshold=False,
            warning=None,
        )

    relaxed_matches = match_receipt_to_transactions(
        receipt,
        transactions,
        config=relaxed_candidate_match_config(),
        merchant_families=merchant_families,
    )
    if relaxed_matches:
        return _ResolvedMatchCandidates(
            matches=list(relaxed_matches),
            used_relaxed_threshold=True,
            warning="No reliable matches found. Showing weaker candidates for manual review.",
        )

    return _ResolvedMatchCandidates(
        matches=[],
        used_relaxed_threshold=False,
        warning="No reliable matches found, and no weaker fallback candidates were found.",
    )


def list_match_candidates_for_receipt(
    approved_receipt_path: Path,
    *,
    ledger_path: Path | None = None,
) -> MatchCandidatesResult:
    """Return candidate matches for one approved receipt."""
    from beanbeaver.application import match_service

    plan = match_service.plan_receipt_match(
        approved_receipt_path,
        ledger_path=ledger_path,
    )
    candidates = [
        MatchCandidate(
            file_path=candidate.file_path,
            line_number=candidate.line_number,
            confidence=candidate.confidence,
            display=candidate.display.strip(),
            payee=candidate.payee,
            narration=candidate.narration,
            date=candidate.date,
            amount=candidate.amount,
        )
        for candidate in plan.candidates
    ]

    return MatchCandidatesResult(
        ledger_path=plan.ledger_path,
        candidates=candidates,
        errors=plan.errors,
        warning=plan.warning,
    )


def apply_match_for_receipt(
    approved_receipt_path: Path,
    *,
    candidate_file_path: str,
    candidate_line_number: int,
    ledger_path: Path | None = None,
) -> ApplyMatchResult:
    """Apply one selected candidate match for an approved receipt."""
    from beanbeaver.application import match_service

    result = match_service.apply_receipt_match(
        approved_receipt_path,
        candidate_file_path=candidate_file_path,
        candidate_line_number=candidate_line_number,
        ledger_path=ledger_path,
    )
    return ApplyMatchResult(
        status=result.status,
        ledger_path=result.ledger_path,
        matched_receipt_path=result.matched_receipt_path,
        enriched_path=result.enriched_path,
        message=result.message,
    )


def _suggest_open_accounts_for_unknown_account(
    unknown_account: str,
    *,
    ledger_path: Path | str | None,
    limit: int = 3,
) -> list[str]:
    """Suggest likely open ledger accounts for an unknown account reference."""
    parts = [part for part in unknown_account.split(":") if part]
    if not parts:
        return []

    patterns: list[str] = []
    if len(parts) >= 2:
        parent = ":".join(parts[:-1])
        patterns.extend([parent, f"{parent}:*"])
    if len(parts) >= 3:
        grandparent = ":".join(parts[:-2])
        patterns.append(f"{grandparent}:*")
    patterns.append(f"{parts[0]}:*")

    seen_patterns: set[str] = set()
    deduped_patterns: list[str] = []
    for pattern in patterns:
        if pattern not in seen_patterns:
            deduped_patterns.append(pattern)
            seen_patterns.add(pattern)

    candidates = open_accounts(deduped_patterns, ledger_path=ledger_path)
    if not candidates:
        return []

    parent_prefix = ":".join(parts[:-1])
    exact_parent = parent_prefix if parent_prefix in candidates else None
    same_parent_descendants = [
        candidate for candidate in candidates if parent_prefix and candidate.startswith(f"{parent_prefix}:")
    ]
    same_parent_descendants.sort(
        key=lambda candidate: (difflib.SequenceMatcher(a=unknown_account, b=candidate).ratio(), candidate),
        reverse=True,
    )
    other_candidates = [
        candidate for candidate in candidates if candidate not in same_parent_descendants and candidate != exact_parent
    ]
    other_candidates.sort(
        key=lambda candidate: (difflib.SequenceMatcher(a=unknown_account, b=candidate).ratio(), candidate),
        reverse=True,
    )

    ranked: list[str] = []
    if same_parent_descendants:
        ranked.append(same_parent_descendants[0])
        if exact_parent is not None:
            ranked.append(exact_parent)
        ranked.extend(same_parent_descendants[1:])
    elif exact_parent is not None:
        ranked.append(exact_parent)
    ranked.extend(other_candidates)

    suggestions: list[str] = []
    seen_accounts: set[str] = set()
    for candidate in ranked:
        if candidate == unknown_account or candidate in seen_accounts:
            continue
        suggestions.append(candidate)
        seen_accounts.add(candidate)
        if len(suggestions) >= limit:
            break
    return suggestions


def _format_match_apply_error(exc: Exception, *, ledger_path: Path | str | None = None) -> list[str]:
    """Render a user-facing error block for receipt match application failures."""
    message = str(exc).strip() or exc.__class__.__name__
    validation_prefix = "ledger validation failed after replacement:"
    if not message.startswith(validation_prefix):
        return [f"  Failed to apply match: {message}"]

    details = message[len(validation_prefix) :].strip()
    file_match = re.search(r"'filename': '([^']+)'", details)
    line_match = re.search(r"'lineno': (\d+)", details)
    message_match = re.search(r'message="([^"]+)"', details)
    if message_match is None:
        message_match = re.search(r"message='([^']+)'", details)

    formatted = ["  Failed to apply match: ledger validation failed after replacement."]
    if file_match and line_match:
        formatted.append(f"    File: {file_match.group(1)}:{line_match.group(1)}")
    elif file_match:
        formatted.append(f"    File: {file_match.group(1)}")

    if message_match:
        formatted.append(f"    Error: {message_match.group(1)}")
    elif details:
        formatted.append(f"    Details: {details}")

    unknown_account_match = re.search(r"unknown account '([^']+)'", details)
    if unknown_account_match:
        unknown_account = unknown_account_match.group(1)
        formatted.append(f"    Unknown account: {unknown_account}")
        suggestions = _suggest_open_accounts_for_unknown_account(
            unknown_account,
            ledger_path=ledger_path,
        )
        if suggestions:
            formatted.append("    Suggestions:")
            for suggestion in suggestions:
                formatted.append(f"      - {suggestion}")

    return formatted


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


def _prompt_failed_match_recovery() -> Literal["edit", "skip"]:
    """Ask the user how to recover from a failed match attempt."""
    print("  Match application failed. Choose next step:")
    print("    1. Re-edit approved receipt")
    print("    2. Skip this receipt")
    while True:
        choice = input("  Select [2]: ").strip().lower()
        if choice in {"", "2", "s", "skip"}:
            return "skip"
        if choice in {"1", "e", "edit"}:
            return "edit"
        print("  Invalid choice. Enter 1 or 2.")


def _format_receipt_inspection(receipt: Receipt, *, path: Path) -> list[str]:
    """Render one approved receipt in a human-readable inspection view."""
    lines = [
        "  Receipt details:",
        f"    File: {path}",
        f"    Merchant: {receipt.merchant or 'UNKNOWN'}",
        f"    Date: {receipt.date.isoformat() if not receipt.date_is_placeholder else 'UNKNOWN'}",
        f"    Total: ${receipt.total:.2f}",
    ]
    if receipt.subtotal is not None:
        lines.append(f"    Subtotal: ${receipt.subtotal:.2f}")
    if receipt.tax is not None:
        lines.append(f"    Tax: ${receipt.tax:.2f}")
    lines.append(f"    Itemized total: ${itemized_receipt_total(receipt):.2f}")

    if receipt.items:
        lines.append("    Items:")
        for index, item in enumerate(receipt.items, 1):
            quantity_suffix = f" x{item.quantity}" if item.quantity != 1 else ""
            category_suffix = f" [{item.category}]" if item.category else ""
            lines.append(f"      {index:>2}. {item.description}{quantity_suffix} - ${item.total:.2f}{category_suffix}")
    else:
        lines.append("    Items: none")

    if receipt.warnings:
        lines.append("    Warnings:")
        for warning in receipt.warnings:
            lines.append(f"      - {warning.message}")

    return lines


def _format_transaction_inspection(match: Any, *, index: int) -> list[str]:
    """Render one candidate ledger transaction with postings for inspection."""
    txn = match.transaction
    amount = transaction_charge_amount(match)
    lines = [
        f"  Candidate [{index}] ({match.confidence:.0%} confidence):",
        f"    File: {match.file_path}:{match.line_number}",
        f"    Date: {txn.date.isoformat()}",
        f"    Payee: {txn.payee or 'UNKNOWN'}",
        f"    Narration: {txn.narration or ''}",
        f"    Charge amount: ${amount:.2f}" if amount is not None else "    Charge amount: UNKNOWN",
        f"    Match details: {match.match_details}",
        "    Postings:",
    ]
    for posting in txn.postings:
        if posting.units is None:
            lines.append(f"      - {posting.account}")
            continue
        lines.append(f"      - {posting.account}: {posting.units.number} {posting.units.currency}")
    return lines


def _print_match_inspection(receipt: Receipt, *, path: Path, display_matches: Sequence[Any]) -> None:
    """Print receipt details plus the currently displayed transaction candidates."""
    print("")
    for line in _format_receipt_inspection(receipt, path=path):
        print(line)
    print("  Candidate transactions:")
    for index, match in enumerate(display_matches, 1):
        for line in _format_transaction_inspection(match, index=index):
            print(line)


def _prompt_match_choice(
    *,
    receipt: Receipt,
    path: Path,
    display_matches: Sequence[Any],
) -> str:
    """Prompt for one match action, supporting inline detail inspection."""
    valid_choices = [str(i) for i in range(1, len(display_matches) + 1)] + ["v", "s", "d", "x", "a", "q"]
    print("    [v] View details | [s] Skip | [d] Delete receipt | [x] Save-and-exit | [a] Abort session")

    while True:
        choice = input("  Select: ").strip().lower()
        if choice == "v":
            _print_match_inspection(receipt, path=path, display_matches=display_matches)
            continue
        if choice in valid_choices:
            return choice
        print(f"    Invalid. Enter one of: {', '.join(valid_choices)}")


def _re_edit_receipt_after_failed_match(
    path: Path,
    *,
    resolve_editor_cmd: ResolveEditorCmd | None,
) -> Path | None:
    """Open the approved receipt in the editor and return its refreshed stage path."""
    from beanbeaver.application.receipts.review import (
        ReEditApprovedReceiptRequest,
        run_re_edit_approved_receipt,
    )

    if resolve_editor_cmd is None:
        print("  Re-edit is unavailable in this entrypoint. Skipping this receipt.")
        return None

    result = run_re_edit_approved_receipt(
        ReEditApprovedReceiptRequest(
            target_path=path,
            resolve_editor_cmd=resolve_editor_cmd,
        )
    )
    if result.status == "editor_not_found":
        editor_cmd = result.editor_cmd or []
        print(f"  Editor not found: {' '.join(editor_cmd)}")
        return None
    if result.status == "editor_failed":
        print(f"  Editor exited with code {result.editor_returncode}.")
        return None
    if result.status == "edited_file_missing":
        print("  Edited file no longer exists. Leaving receipt unchanged.")
        return None
    if result.status == "normalize_failed":
        print(f"  Re-edit saved, but could not normalize filename: {result.normalize_error}")
        return None
    if result.updated_path is None:
        print("  Approved receipt update failed.")
        return None

    print(f"  Updated approved receipt: {result.updated_path}")
    return result.updated_path


def cmd_match(args: argparse.Namespace) -> None:
    """Match all approved receipts against ledger."""
    from beanbeaver.receipt.beancount_rendering import format_enriched_transaction
    from beanbeaver.receipt.matcher import format_match_for_display
    from beanbeaver.runtime.receipt_storage import (
        delete_receipt,
        list_approved_receipts,
        list_scanned_receipts,
        move_to_matched,
        parse_receipt_from_stage_json,
    )

    if not sys.stdin.isatty():
        print("Error: bb match requires an interactive TTY.")
        sys.exit(1)

    if not _ensure_git_clean_before_match():
        return

    scanned = list_scanned_receipts()
    if scanned:
        print(
            f"Warning: {len(scanned)} receipt(s) still in receipts/json/scanned/. "
            "Review with `bb edit` to move them to approved."
        )

    ledger_arg = getattr(args, "ledger", None)
    ledger_path = Path(ledger_arg) if ledger_arg else get_paths().main_beancount
    if not ledger_path.exists():
        logger.error("Ledger file not found: %s", ledger_path)
        print(f"Error: Ledger file not found: {ledger_path}")
        sys.exit(1)

    print(f"Loading ledger from {ledger_path}...")
    snapshot = list_transactions(ledger_path=ledger_path)
    if snapshot.errors:
        print(f"Error: ledger has {len(snapshot.errors)} Beancount error(s). Fix ledger errors before matching.")
        for line in _format_ledger_errors(snapshot.errors, limit=5):
            print(f"  - {line}")
        if len(snapshot.errors) > 5:
            print(f"  ... and {len(snapshot.errors) - 5} more")
        return
    transactions = snapshot.transactions
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
    abort_requested = False
    applied_undo_log: list[_AppliedMatchUndo] = []
    resolve_editor_cmd = getattr(args, "resolve_editor_cmd", None)

    for selected in selected_receipts:
        path = selected[0]
        while True:
            receipt = parse_receipt_from_stage_json(path)
            merchant = receipt.merchant
            receipt_date = None if receipt.date_is_placeholder else receipt.date
            amount = receipt.total

            date_str = receipt_date.isoformat() if receipt_date else "UNKNOWN"
            print(f"\n{path.name}")
            amount_str = f"${amount:.2f}"
            print(f"  {merchant or 'UNKNOWN'} | {date_str} | {amount_str}")

            resolved_matches = _resolve_receipt_match_candidates(
                receipt,
                transactions,
                merchant_families=load_merchant_families(),
            )
            matches = resolved_matches.matches
            if resolved_matches.warning:
                print(f"  Warning: {resolved_matches.warning}")
            available_matches = [m for m in matches if match_key(m) not in used_matches]

            if not available_matches and matches:
                print("  All candidates were already used in this run.")
                while True:
                    reuse_choice = (
                        input("  [u] Show used candidates | [s] Skip | [x] Save-and-exit | [a] Abort session: ")
                        .strip()
                        .lower()
                    )
                    if reuse_choice in {"s", "skip"}:
                        print("  Skipped")
                        skipped_count += 1
                        break
                    if reuse_choice in {"x", "q", "quit"}:
                        print("Stopping matching session.")
                        stopped_early = True
                        break
                    if reuse_choice in {"a", "abort"}:
                        print("Aborting matching session and reverting this run...")
                        abort_requested = True
                        stopped_early = True
                        break
                    if reuse_choice in {"u", "use"}:
                        available_matches = matches
                        break
                    print("  Invalid choice. Enter u, s, x, or a.")
                if stopped_early:
                    break
                if not available_matches:
                    break

            if not matches:
                print("  No matches found - keeping in approved")
                skipped_count += 1
                break

            match_label = "weak candidate(s)" if resolved_matches.used_relaxed_threshold else "match(es)"
            print(f"  Found {len(matches)} {match_label}, {len(available_matches)} available:")
            display_matches = available_matches[:5]
            for i, match in enumerate(display_matches, 1):
                already_used = " (already used)" if match_key(match) in used_matches else ""
                formatted = format_match_for_display(match).strip().replace(chr(10), chr(10) + "        ")
                print(f"    [{i}] {formatted}{already_used}")

            choice = _prompt_match_choice(
                receipt=receipt,
                path=path,
                display_matches=display_matches,
            )

            if choice == "d":
                delete_receipt(path)
                print("  Deleted")
                break
            if choice == "s":
                print("  Skipped")
                skipped_count += 1
                break
            if choice in {"x", "q"}:
                print("Stopping matching session.")
                stopped_early = True
                break
            if choice == "a":
                print("Aborting matching session and reverting this run...")
                abort_requested = True
                stopped_early = True
                break

            selected_idx = int(choice) - 1
            selected_match = display_matches[selected_idx]
            key = match_key(selected_match)
            if key in used_matches:
                confirm = input("  Candidate already used earlier. Reuse it? [y/N]: ").strip().lower()
                if confirm not in {"y", "yes"}:
                    print("  Skipped")
                    skipped_count += 1
                    break

            matched_file = Path(selected_match.file_path)
            if str(matched_file) == "unknown" or not matched_file.exists():
                print(f"  Match target file missing: {selected_match.file_path}")
                skipped_count += 1
                break

            expected_total = transaction_charge_amount(selected_match)
            itemized_total = itemized_receipt_total(receipt)
            if expected_total is not None:
                delta = expected_total - itemized_total
                if delta < Decimal("-0.01"):
                    print(
                        "  Failed to apply match: itemized receipt total "
                        f"(${itemized_total:.2f}) exceeds card transaction (${expected_total:.2f}) "
                        f"by ${abs(delta):.2f}."
                    )
                    recovery = _prompt_failed_match_recovery()
                    if recovery == "skip":
                        print("  Skipped")
                        skipped_count += 1
                        break
                    updated_path = _re_edit_receipt_after_failed_match(
                        path,
                        resolve_editor_cmd=resolve_editor_cmd,
                    )
                    if updated_path is not None:
                        path = updated_path
                    print("  Recomputing matches after re-edit...")
                    continue

            receipt_name = _receipt_chain_name(path)
            enriched = format_enriched_transaction(receipt, selected_match)
            enriched_dir = matched_file.parent / "_enriched"
            enriched_dir.mkdir(parents=True, exist_ok=True)
            enriched_path = enriched_dir / f"{receipt_name}.beancount"
            include_rel = enriched_path.relative_to(matched_file.parent).as_posix()
            ledger_snapshot = snapshot_receipt_match_files(
                statement_path=matched_file,
                enriched_path=enriched_path,
            )

            try:
                status = apply_receipt_match(
                    ledger_path=ledger_path,
                    statement_path=matched_file,
                    line_number=selected_match.line_number,
                    include_rel_path=include_rel,
                    receipt_name=receipt_name,
                    enriched_path=enriched_path,
                    enriched_content=enriched,
                )

                matched_receipt_path = move_to_matched(path)
                action_msg = "already applied; receipt archived" if status == "already_applied" else "applied"
                print(f"  Matched! Transaction {action_msg}. Enriched file: {enriched_path}")
                matched_count += 1
                used_matches.add(key)
                applied_undo_log.append(
                    _AppliedMatchUndo(
                        approved_receipt_path=path,
                        matched_receipt_path=matched_receipt_path,
                        ledger_snapshot=ledger_snapshot,
                    )
                )

                # Reload transactions so next matches use updated line numbers/content.
                reloaded = list_transactions(ledger_path=ledger_path)
                if reloaded.errors:
                    print("  Warning: ledger reload has errors; stopping session.")
                    stopped_early = True
                else:
                    transactions = reloaded.transactions
                break
            except Exception as exc:
                for line in _format_match_apply_error(exc, ledger_path=ledger_path):
                    print(line)
                recovery = _prompt_failed_match_recovery()
                if recovery == "skip":
                    print("  Skipped")
                    skipped_count += 1
                    break
                updated_path = _re_edit_receipt_after_failed_match(
                    path,
                    resolve_editor_cmd=resolve_editor_cmd,
                )
                if updated_path is not None:
                    path = updated_path
                print("  Recomputing matches after re-edit...")
                continue

        if stopped_early:
            break

    if abort_requested:
        reverted_count, rollback_warnings = _rollback_applied_matches(applied_undo_log)
        matched_count = max(matched_count - reverted_count, 0)
        print(f"Aborted. Reverted {reverted_count} applied match(es).")
        for warning in rollback_warnings:
            print(f"  Rollback warning: {warning}")

    print("\n" + "=" * 60)
    if stopped_early:
        print("Stopped early by user.")
    print(f"Done. Matched: {matched_count}, Skipped: {skipped_count}")
