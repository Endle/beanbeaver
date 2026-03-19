"""Storage and retrieval of staged receipt JSON artifacts."""

from __future__ import annotations

import json
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from beanbeaver.domain.receipt import Receipt
from beanbeaver.receipt.beancount_rendering import render_stage_document_as_beancount
from beanbeaver.receipt.receipt_structuring import (
    build_parsed_receipt_stage,
    clone_stage_document,
    get_receipt_id,
    get_stage_index,
    get_stage_summary,
    load_stage_document,
    receipt_from_stage_document,
    save_stage_document,
)
from beanbeaver.runtime import (
    get_logger,
    get_paths,
    load_item_category_rule_layers,
    load_receipt_structuring_rule_layers,
)

logger = get_logger(__name__)


def _project_paths():
    return get_paths()


def _receipts_root() -> Path:
    return _project_paths().receipts


def ensure_directories() -> None:
    """Create required receipt directories if they do not exist."""
    _project_paths().ensure_receipt_directories()


def _next_available_dir(path: Path) -> Path:
    """Return a unique directory path when collisions exist."""
    if not path.exists():
        return path

    counter = 1
    while True:
        candidate = path.parent / f"{path.name}_{counter}"
        if not candidate.exists():
            return candidate
        counter += 1

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


def _stage_status(document: dict[str, Any]) -> str:
    """Resolve one stage document into its queue status."""
    stage = str((document.get("meta") or {}).get("stage") or "").lower()
    if "matched" in stage:
        return "matched"
    if "review" in stage:
        return "approved"
    return "scanned"


def _stage_kind(document: dict[str, Any]) -> str:
    """Return the canonical stage kind name for filenames."""
    status = _stage_status(document)
    if status == "matched":
        return "matched"
    if status == "approved":
        return "review"
    return "parsed"


def _canonical_stage_filename(document: dict[str, Any]) -> str:
    """Return the canonical staged JSON filename for a document."""
    kind = _stage_kind(document)
    if kind == "matched":
        return "900_matched.receipt.json"
    return f"{get_stage_index(document) * 10:03d}_{kind}.receipt.json"


def _source_dir(receipt_dir: Path) -> Path:
    return receipt_dir / _project_paths().receipts_source_dirname


def _ocr_dir(receipt_dir: Path) -> Path:
    return receipt_dir / _project_paths().receipts_ocr_dirname


def _stages_dir(receipt_dir: Path) -> Path:
    return receipt_dir / _project_paths().receipts_stages_dirname


def _rendered_dir(receipt_dir: Path) -> Path:
    return receipt_dir / _project_paths().receipts_rendered_dirname


def _current_receipt_path(receipt_dir: Path) -> Path:
    return receipt_dir / "current.receipt.json"


def _rendered_current_path(receipt_dir: Path) -> Path:
    return _rendered_dir(receipt_dir) / "current.beancount"


def _receipt_meta_path(receipt_dir: Path) -> Path:
    return receipt_dir / "meta.json"

def _ensure_receipt_dir(receipt_dir: Path) -> None:
    receipt_dir.mkdir(parents=True, exist_ok=True)
    _source_dir(receipt_dir).mkdir(parents=True, exist_ok=True)
    _ocr_dir(receipt_dir).mkdir(parents=True, exist_ok=True)
    _stages_dir(receipt_dir).mkdir(parents=True, exist_ok=True)
    _rendered_dir(receipt_dir).mkdir(parents=True, exist_ok=True)


def receipt_dir_from_stage_path(stage_path: Path) -> Path:
    """Public helper returning the canonical receipt directory for a stage path."""
    return _receipt_dir_for_stage_path(_canonicalize_input_stage_path(stage_path))


def receipt_source_original_path(receipt_dir: Path, *, suffix: str = ".jpg") -> Path:
    """Return the canonical original-image path for one receipt directory."""
    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    return _source_dir(receipt_dir) / f"original{normalized_suffix.lower()}"


def receipt_source_resized_path(receipt_dir: Path) -> Path:
    """Return the canonical resized-image path for one receipt directory."""
    return _source_dir(receipt_dir) / "resized.jpg"


def receipt_ocr_raw_path(receipt_dir: Path) -> Path:
    """Return the canonical raw OCR JSON path for one receipt directory."""
    return _ocr_dir(receipt_dir) / "raw.json"


def receipt_ocr_stage1_path(receipt_dir: Path) -> Path:
    """Return the canonical stage-1 OCR JSON path for one receipt directory."""
    return _ocr_dir(receipt_dir) / "stage1.json"


def receipt_ocr_overlay_path(receipt_dir: Path) -> Path:
    """Return the canonical OCR debug overlay path for one receipt directory."""
    return _ocr_dir(receipt_dir) / "overlay.jpg"


def write_receipt_source_artifacts(
    receipt_dir: Path,
    *,
    original_image_path: Path | None = None,
    resized_image_bytes: bytes | None = None,
) -> tuple[Path | None, Path | None]:
    """Write canonical source artifacts for one receipt chain."""
    _ensure_receipt_dir(receipt_dir)

    original_target: Path | None = None
    if original_image_path is not None:
        suffix = original_image_path.suffix or ".jpg"
        original_target = receipt_source_original_path(receipt_dir, suffix=suffix)
        if original_image_path.resolve() != original_target.resolve():
            shutil.copy2(original_image_path, original_target)
        elif not original_target.exists():
            original_target.write_bytes(original_image_path.read_bytes())

    resized_target: Path | None = None
    if resized_image_bytes is not None:
        resized_target = receipt_source_resized_path(receipt_dir)
        resized_target.write_bytes(resized_image_bytes)

    return original_target, resized_target


def write_receipt_ocr_artifacts(
    receipt_dir: Path,
    *,
    raw_ocr_payload: dict[str, Any] | None = None,
    stage1_ocr_payload: dict[str, Any] | None = None,
) -> tuple[Path | None, Path | None]:
    """Write canonical OCR JSON artifacts for one receipt chain."""
    _ensure_receipt_dir(receipt_dir)

    raw_path: Path | None = None
    if raw_ocr_payload is not None:
        raw_path = receipt_ocr_raw_path(receipt_dir)
        raw_path.write_text(json.dumps(raw_ocr_payload, indent=2) + "\n", encoding="utf-8")

    stage1_path: Path | None = None
    if stage1_ocr_payload is not None:
        stage1_path = receipt_ocr_stage1_path(receipt_dir)
        stage1_path.write_text(json.dumps(stage1_ocr_payload, indent=2) + "\n", encoding="utf-8")

    return raw_path, stage1_path


def _write_receipt_meta(receipt_dir: Path, document: dict[str, Any], *, latest_stage_path: Path) -> None:
    merchant, receipt_date, total = get_stage_summary(document)
    payload = {
        "receipt_id": get_receipt_id(document),
        "latest_stage_file": latest_stage_path.name,
        "latest_stage_path": str(latest_stage_path.relative_to(receipt_dir)),
        "status": _stage_status(document),
        "merchant": merchant,
        "date": receipt_date.isoformat() if receipt_date is not None else None,
        "total": f"{total:.2f}" if total is not None else None,
    }
    _receipt_meta_path(receipt_dir).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_current_artifacts(receipt_dir: Path, stage_path: Path) -> tuple[Path, Path]:
    document = load_stage_document(stage_path)
    current_path = _current_receipt_path(receipt_dir)
    save_stage_document(current_path, document)

    rendered_path = _rendered_current_path(receipt_dir)
    rendered_path.write_text(
        render_stage_document_as_beancount(
            document,
            rule_layers=load_item_category_rule_layers(),
        ),
        encoding="utf-8",
    )
    _write_receipt_meta(receipt_dir, document, latest_stage_path=stage_path)
    return current_path, rendered_path
def _is_canonical_receipt_dir(path: Path) -> bool:
    return path.is_dir() and (_stages_dir(path).exists() or _current_receipt_path(path).exists())


def _receipt_dir_for_stage_path(stage_path: Path) -> Path:
    """Resolve the enclosing canonical receipt directory for a stage path."""
    if stage_path.name == "current.receipt.json":
        return stage_path.parent
    if stage_path.parent.name == _project_paths().receipts_stages_dirname:
        return stage_path.parent.parent
    return stage_path.parent


def _stage_files(receipt_dir: Path) -> list[Path]:
    """List all stage JSON files for a receipt chain."""
    canonical_stage_dir = _stages_dir(receipt_dir)
    if canonical_stage_dir.exists():
        return sorted(canonical_stage_dir.glob("*.receipt.json"))
    return sorted(receipt_dir.glob("*.receipt.json"))


def _latest_stage_path(receipt_dir: Path) -> Path:
    """Return the latest stage file in a receipt chain directory."""
    stage_files = _stage_files(receipt_dir)
    if not stage_files:
        raise FileNotFoundError(f"No stage JSON files found in {receipt_dir}")
    return max(stage_files, key=lambda path: get_stage_index(load_stage_document(path)))


def _canonicalize_input_stage_path(stage_path: Path) -> Path:
    """Normalize supported canonical stage paths."""
    path = stage_path.resolve()
    if not path.exists():
        return path

    if path.is_dir() and _is_canonical_receipt_dir(path):
        return _latest_stage_path(path)
    if path.name == "current.receipt.json" and _is_canonical_receipt_dir(path.parent):
        return _latest_stage_path(path.parent)
    if path.parent.name == _project_paths().receipts_stages_dirname and _is_canonical_receipt_dir(path.parent.parent):
        return path
    return path


def _normalize_receipt_dir(stage_path: Path) -> Path:
    """Rename the canonical receipt chain directory to match current effective values."""
    document = load_stage_document(stage_path)
    current_dir = _receipt_dir_for_stage_path(stage_path)
    desired_dir = _receipts_root() / _receipt_dir_name(document)
    if desired_dir != current_dir:
        target_dir = desired_dir if not desired_dir.exists() else _next_available_dir(desired_dir)
        relative_stage = stage_path.relative_to(current_dir)
        current_dir.rename(target_dir)
        current_dir = target_dir
        stage_path = current_dir / relative_stage

    desired_stage_path = _stages_dir(current_dir) / _canonical_stage_filename(document)
    _ensure_receipt_dir(current_dir)
    if stage_path != desired_stage_path:
        stage_path.rename(desired_stage_path)
        stage_path = desired_stage_path
    return stage_path


def _status_matches(document: dict[str, Any], *, expected: str) -> bool:
    return _stage_status(document) == expected


def _iter_canonical_receipt_dirs() -> list[Path]:
    ensure_directories()
    return sorted(path for path in _receipts_root().iterdir() if _is_canonical_receipt_dir(path))


def save_scanned_receipt(
    receipt: Receipt,
    *,
    raw_ocr_payload: dict[str, Any] | None = None,
    stage1_ocr_payload: dict[str, Any] | None = None,
    image_sha256: str | None = None,
    source_image_path: Path | None = None,
    resized_image_bytes: bytes | None = None,
) -> Path:
    """Persist the initial parsed receipt stage and rendered Beancount draft."""
    ensure_directories()
    temp_document = build_parsed_receipt_stage(
        receipt,
        rule_layers=load_receipt_structuring_rule_layers(),
        raw_ocr_payload=raw_ocr_payload,
        ocr_json_path=None,
        image_sha256=image_sha256,
    )
    receipt_dir = _next_available_dir(_receipts_root() / _receipt_dir_name(temp_document))
    _ensure_receipt_dir(receipt_dir)

    raw_ocr_path, _ = write_receipt_ocr_artifacts(
        receipt_dir,
        raw_ocr_payload=raw_ocr_payload,
        stage1_ocr_payload=stage1_ocr_payload,
    )
    write_receipt_source_artifacts(
        receipt_dir,
        original_image_path=source_image_path,
        resized_image_bytes=resized_image_bytes,
    )

    document = build_parsed_receipt_stage(
        receipt,
        rule_layers=load_receipt_structuring_rule_layers(),
        raw_ocr_payload=raw_ocr_payload,
        ocr_json_path=str(raw_ocr_path.relative_to(_project_paths().receipts)) if raw_ocr_path else None,
        image_sha256=image_sha256,
    )
    stage_path = _stages_dir(receipt_dir) / _canonical_stage_filename(document)
    save_stage_document(stage_path, document)
    _write_current_artifacts(receipt_dir, stage_path)
    latest_stage_path = stage_path
    logger.info("Saved scanned receipt JSON to %s", latest_stage_path)
    return latest_stage_path


def create_next_review_stage(
    stage_path: Path,
    *,
    created_by: str = "human_review",
    pass_name: str = "manual_review",
) -> Path:
    """Create the next review stage file from the current latest stage."""
    stage_path = _canonicalize_input_stage_path(stage_path)
    document = load_stage_document(stage_path)
    current_index = get_stage_index(document)
    next_stage_name = f"review_stage_{current_index + 1}"
    next_document = clone_stage_document(
        document,
        stage=next_stage_name,
        created_by=created_by,
        pass_name=pass_name,
        parent_file=stage_path.name,
    )
    receipt_dir = _receipt_dir_for_stage_path(stage_path)
    _ensure_receipt_dir(receipt_dir)
    next_path = _stages_dir(receipt_dir) / _canonical_stage_filename(next_document)
    save_stage_document(next_path, next_document)
    return next_path


def refresh_stage_artifacts(stage_path: Path) -> tuple[Path, Path]:
    """Normalize one stage path and refresh its canonical current artifacts."""
    ensure_directories()
    canonical_stage_path = _canonicalize_input_stage_path(stage_path)
    normalized_stage_path = _normalize_receipt_dir(canonical_stage_path)
    receipt_dir = _receipt_dir_for_stage_path(normalized_stage_path)
    _, rendered_path = _write_current_artifacts(receipt_dir, normalized_stage_path)
    return normalized_stage_path, rendered_path


def move_scanned_to_approved(stage_path: Path) -> Path:
    """Promote one scanned receipt chain into approved status."""
    ensure_directories()
    canonical_stage_path = _canonicalize_input_stage_path(stage_path)
    document = load_stage_document(canonical_stage_path)
    if _stage_status(document) == "scanned":
        canonical_stage_path = create_next_review_stage(
            canonical_stage_path,
            created_by="storage_transition",
            pass_name="approve_receipt",
        )
    normalized_stage_path, _ = refresh_stage_artifacts(canonical_stage_path)
    logger.info("Approved %s -> %s", stage_path, normalized_stage_path)
    return normalized_stage_path


def move_to_matched(stage_path: Path) -> Path:
    """Promote one approved receipt chain into matched status."""
    ensure_directories()
    canonical_stage_path = _canonicalize_input_stage_path(stage_path)
    document = load_stage_document(canonical_stage_path)
    if _stage_status(document) != "matched":
        matched_document = clone_stage_document(
            document,
            stage="matched",
            created_by="storage_transition",
            pass_name="apply_match",
            parent_file=canonical_stage_path.name,
        )
        receipt_dir = _receipt_dir_for_stage_path(canonical_stage_path)
        matched_path = _stages_dir(receipt_dir) / _canonical_stage_filename(matched_document)
        save_stage_document(matched_path, matched_document)
        canonical_stage_path = matched_path

    normalized_stage_path, _ = refresh_stage_artifacts(canonical_stage_path)
    logger.info("Matched %s -> %s", stage_path, normalized_stage_path)
    return normalized_stage_path


def parse_receipt_from_stage_json(filepath: Path) -> Receipt:
    """Resolve a stage JSON file into an effective Receipt."""
    document = load_stage_document(_canonicalize_input_stage_path(filepath))
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


def _list_stage_receipts_by_status(*, status: str) -> list[Path]:
    results: list[Path] = []
    for receipt_dir in _iter_canonical_receipt_dirs():
        try:
            stage_path = _latest_stage_path(receipt_dir)
        except FileNotFoundError:
            continue
        if _status_matches(load_stage_document(stage_path), expected=status):
            results.append(stage_path)
    return sorted(results, key=lambda path: path.parent.parent.name)


def list_approved_stage_receipts() -> list[Path]:
    """Return latest approved stage files."""
    return _list_stage_receipts_by_status(status="approved")


def list_scanned_receipts() -> list[Path]:
    """Return latest scanned stage files."""
    return _list_stage_receipts_by_status(status="scanned")


def list_approved_receipts() -> list[tuple[Path, str | None, date | None, Decimal | None]]:
    """List approved receipt summaries from latest approved stages."""
    ensure_directories()
    results: list[tuple[Path, str | None, date | None, Decimal | None]] = []
    for stage_path in list_approved_stage_receipts():
        merchant, receipt_date, total = get_stage_summary(load_stage_document(stage_path))
        results.append((stage_path, merchant, receipt_date, total))
    return results


def _remove_tree(path: Path) -> None:
    for child in sorted(path.iterdir(), reverse=True):
        if child.is_dir():
            _remove_tree(child)
        else:
            child.unlink()
    path.rmdir()


def delete_receipt(receipt_path: Path) -> bool:
    """Delete one receipt chain and its canonical artifacts."""
    canonical_path = _canonicalize_input_stage_path(receipt_path)
    if not canonical_path.exists():
        return False

    receipt_dir = canonical_path if canonical_path.is_dir() else _receipt_dir_for_stage_path(canonical_path)
    if not receipt_dir.exists():
        return False

    _remove_tree(receipt_dir)
    logger.info("Deleted %s", receipt_dir)
    return True
