"""Storage and retrieval of staged receipt JSON artifacts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from beanbeaver.domain.receipt import Receipt
from beanbeaver.receipt.staged_json import (
    build_parsed_receipt_stage,
    clone_stage_document,
    get_receipt_id,
    get_stage_index,
    get_stage_summary,
    load_stage_document,
    receipt_from_stage_document,
    render_stage_document_as_beancount,
    save_stage_document,
)
from beanbeaver.runtime import get_logger, get_paths, load_item_category_rule_layers

logger = get_logger(__name__)

_paths = get_paths()
SCANNED_DIR = _paths.receipts_json_scanned
APPROVED_DIR = _paths.receipts_json_approved
MATCHED_DIR = _paths.receipts_json_matched
RENDERED_SCANNED_DIR = _paths.receipts_rendered_scanned
RENDERED_APPROVED_DIR = _paths.receipts_rendered_approved
RENDERED_MATCHED_DIR = _paths.receipts_rendered_matched


def ensure_directories() -> None:
    """Create required receipt directories if they do not exist."""
    _paths.ensure_receipt_directories()


def _slug(text: str | None) -> str:
    """Return a filesystem-safe slug."""
    if not text:
        return "unknown"
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in text.lower())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned[:30] or "unknown"


def _date_component(value: date | None) -> str:
    """Format receipt date for filesystem use."""
    return value.isoformat() if value is not None else "unknown-date"


def _amount_component(value: Decimal | None) -> str:
    """Format receipt total for filesystem use."""
    if value is None:
        return "unknown_total"
    return f"{value:.2f}".replace(".", "_")


def _receipt_dir_name(document: dict[str, Any]) -> str:
    """Build a human-readable receipt-chain directory name."""
    merchant, receipt_date, total = get_stage_summary(document)
    receipt_id = get_receipt_id(document)
    suffix = receipt_id[:4] if receipt_id else "unkn"
    return f"{_date_component(receipt_date)}_{_slug(merchant)}_{_amount_component(total)}_{suffix}"


def _rendered_filename(document: dict[str, Any]) -> str:
    """Return the rendered Beancount filename for a stage document."""
    return f"{_receipt_dir_name(document)}.beancount"


def _stage_files(receipt_dir: Path) -> list[Path]:
    """List all stage JSON files for a receipt chain."""
    return sorted(receipt_dir.glob("*.receipt.json"))


def _latest_stage_path(receipt_dir: Path) -> Path:
    """Return the latest stage file in a receipt chain directory."""
    stage_files = _stage_files(receipt_dir)
    if not stage_files:
        raise FileNotFoundError(f"No stage JSON files found in {receipt_dir}")
    return max(stage_files, key=lambda path: get_stage_index(load_stage_document(path)))


def _normalize_receipt_dir(stage_path: Path) -> Path:
    """Rename the receipt chain directory to match current effective values."""
    document = load_stage_document(stage_path)
    current_dir = stage_path.parent
    desired_dir = current_dir.parent / _receipt_dir_name(document)
    if desired_dir == current_dir:
        return stage_path

    target_dir = desired_dir
    counter = 1
    while target_dir.exists():
        target_dir = desired_dir.parent / f"{desired_dir.name}_{counter}"
        counter += 1

    current_dir.rename(target_dir)
    return target_dir / stage_path.name


def _write_rendered_output(stage_path: Path, *, rendered_root: Path) -> tuple[Path, Path]:
    """Render Beancount output for a stage and normalize its artifact names."""
    ensure_directories()
    old_dir_name = stage_path.parent.name
    normalized_stage_path = _normalize_receipt_dir(stage_path)
    document = load_stage_document(normalized_stage_path)
    rendered_path = rendered_root / _rendered_filename(document)
    rendered_path.write_text(
        render_stage_document_as_beancount(
            document,
            rule_layers=load_item_category_rule_layers(),
        )
    )

    stale_rendered = rendered_root / f"{old_dir_name}.beancount"
    if stale_rendered != rendered_path and stale_rendered.exists():
        stale_rendered.unlink()

    return normalized_stage_path, rendered_path


def _status_roots_for_path(stage_path: Path) -> tuple[Path, Path]:
    """Return (json_root, rendered_root) for a stage path."""
    path = stage_path.resolve()
    candidates = (
        (SCANNED_DIR.resolve(), RENDERED_SCANNED_DIR),
        (APPROVED_DIR.resolve(), RENDERED_APPROVED_DIR),
        (MATCHED_DIR.resolve(), RENDERED_MATCHED_DIR),
    )
    for json_root, rendered_root in candidates:
        try:
            path.relative_to(json_root)
            return json_root, rendered_root
        except ValueError:
            continue
    raise ValueError(f"Receipt stage path is outside known storage roots: {stage_path}")


def save_scanned_receipt(
    receipt: Receipt,
    *,
    raw_ocr_payload: dict[str, Any] | None = None,
    image_sha256: str | None = None,
    ocr_json_path: Path | None = None,
) -> Path:
    """Persist the initial parsed receipt stage and rendered Beancount draft."""
    ensure_directories()
    document = build_parsed_receipt_stage(
        receipt,
        rule_layers=load_item_category_rule_layers(),
        raw_ocr_payload=raw_ocr_payload,
        ocr_json_path=str(ocr_json_path.relative_to(_paths.receipts)) if ocr_json_path else None,
        image_sha256=image_sha256,
    )
    receipt_dir = SCANNED_DIR / _receipt_dir_name(document)
    receipt_dir.mkdir(parents=True, exist_ok=False)
    stage_path = receipt_dir / "parsed.receipt.json"
    save_stage_document(stage_path, document)
    normalized_stage_path, _ = _write_rendered_output(stage_path, rendered_root=RENDERED_SCANNED_DIR)
    logger.info("Saved scanned receipt JSON to %s", normalized_stage_path)
    return normalized_stage_path


def create_next_review_stage(
    stage_path: Path,
    *,
    created_by: str = "human_review",
    pass_name: str = "manual_review",
) -> Path:
    """Create the next review stage file from the current latest stage."""
    document = load_stage_document(stage_path)
    current_index = get_stage_index(document)
    next_index = current_index + 1
    next_stage_name = f"review_stage_{next_index}"
    next_filename = f"{next_stage_name}.receipt.json"
    next_document = clone_stage_document(
        document,
        stage=next_stage_name,
        created_by=created_by,
        pass_name=pass_name,
        parent_file=stage_path.name,
    )
    next_path = stage_path.parent / next_filename
    save_stage_document(next_path, next_document)
    return next_path


def refresh_stage_artifacts(stage_path: Path) -> tuple[Path, Path]:
    """Normalize one stage path and refresh its rendered Beancount output."""
    _, rendered_root = _status_roots_for_path(stage_path)
    return _write_rendered_output(stage_path, rendered_root=rendered_root)


def move_scanned_to_approved(stage_path: Path) -> Path:
    """Move one scanned receipt chain to the approved JSON root."""
    ensure_directories()
    stage_path = stage_path.resolve()
    stage_path.relative_to(SCANNED_DIR.resolve())

    receipt_dir = stage_path.parent
    target_dir = APPROVED_DIR / receipt_dir.name
    counter = 1
    while target_dir.exists():
        target_dir = APPROVED_DIR / f"{receipt_dir.name}_{counter}"
        counter += 1

    old_rendered = RENDERED_SCANNED_DIR / f"{receipt_dir.name}.beancount"
    receipt_dir.rename(target_dir)
    if old_rendered.exists():
        old_rendered.unlink()

    new_stage_path = target_dir / stage_path.name
    normalized_stage_path, _ = _write_rendered_output(new_stage_path, rendered_root=RENDERED_APPROVED_DIR)
    logger.info("Moved %s to %s", stage_path, normalized_stage_path)
    return normalized_stage_path


def move_to_matched(stage_path: Path) -> Path:
    """Move one approved receipt chain to the matched JSON root."""
    ensure_directories()
    stage_path = stage_path.resolve()
    stage_path.relative_to(APPROVED_DIR.resolve())

    receipt_dir = stage_path.parent
    target_dir = MATCHED_DIR / receipt_dir.name
    counter = 1
    while target_dir.exists():
        target_dir = MATCHED_DIR / f"{receipt_dir.name}_{counter}"
        counter += 1

    old_rendered = RENDERED_APPROVED_DIR / f"{receipt_dir.name}.beancount"
    receipt_dir.rename(target_dir)
    if old_rendered.exists():
        old_rendered.unlink()

    new_stage_path = target_dir / stage_path.name
    normalized_stage_path, _ = _write_rendered_output(new_stage_path, rendered_root=RENDERED_MATCHED_DIR)
    logger.info("Moved %s to %s", stage_path, normalized_stage_path)
    return normalized_stage_path


def parse_receipt_from_stage_json(filepath: Path) -> Receipt:
    """Resolve a stage JSON file into an effective Receipt."""
    document = load_stage_document(filepath)
    return receipt_from_stage_document(document, rule_layers=load_item_category_rule_layers())


def load_approved_receipts(
    date_filter: date | None = None,
    amount_filter: Decimal | None = None,
    tolerance_days: int = 3,
    amount_tolerance: Decimal = Decimal("0.10"),
) -> list[tuple[Path, Receipt]]:
    """Load approved receipts, optionally filtered by effective date/amount."""
    ensure_directories()
    results: list[tuple[Path, Receipt]] = []

    for stage_path in list_approved_stage_receipts():
        receipt = parse_receipt_from_stage_json(stage_path)
        if date_filter and not receipt.date_is_placeholder:
            if abs((receipt.date - date_filter).days) > tolerance_days:
                continue
        if amount_filter is not None and abs(receipt.total - amount_filter) > amount_tolerance:
            continue
        results.append((stage_path, receipt))

    return results


def list_approved_stage_receipts() -> list[Path]:
    """Return latest approved stage files."""
    ensure_directories()
    return sorted(
        (_latest_stage_path(receipt_dir) for receipt_dir in APPROVED_DIR.iterdir() if receipt_dir.is_dir()),
        key=lambda path: path.parent.name,
    )


def list_scanned_receipts() -> list[Path]:
    """Return latest scanned stage files."""
    ensure_directories()
    return sorted(
        (_latest_stage_path(receipt_dir) for receipt_dir in SCANNED_DIR.iterdir() if receipt_dir.is_dir()),
        key=lambda path: path.parent.name,
    )


def list_approved_receipts() -> list[tuple[Path, str | None, date | None, Decimal | None]]:
    """List approved receipt summaries from latest approved stages."""
    ensure_directories()
    results: list[tuple[Path, str | None, date | None, Decimal | None]] = []
    for stage_path in list_approved_stage_receipts():
        merchant, receipt_date, total = get_stage_summary(load_stage_document(stage_path))
        results.append((stage_path, merchant, receipt_date, total))
    return results


def delete_receipt(receipt_path: Path) -> bool:
    """Delete one receipt chain and its rendered Beancount output."""
    if not receipt_path.exists():
        return False

    receipt_dir = receipt_path if receipt_path.is_dir() else receipt_path.parent
    try:
        _, rendered_root = _status_roots_for_path(receipt_path if receipt_path.is_file() else _latest_stage_path(receipt_dir))
    except Exception:
        rendered_root = None

    rendered_path = None
    if rendered_root is not None and receipt_dir.exists():
        stage_path = _latest_stage_path(receipt_dir)
        document = load_stage_document(stage_path)
        rendered_path = rendered_root / _rendered_filename(document)

    for child in sorted(receipt_dir.glob("*"), reverse=True):
        if child.is_file():
            child.unlink()
    receipt_dir.rmdir()

    if rendered_path is not None and rendered_path.exists():
        rendered_path.unlink()

    logger.info("Deleted %s", receipt_dir)
    return True
