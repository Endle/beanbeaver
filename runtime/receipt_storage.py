"""Storage and retrieval of staged receipt JSON artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from beanbeaver.domain.receipt import Receipt, ReceiptItem
from beanbeaver.receipt.beancount_rendering import render_stage_document_as_beancount
from beanbeaver.receipt.date_utils import placeholder_receipt_date
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

_LEGACY_RECEIPT_DIR_NAMES = {
    "json",
    "rendered",
    "images",
    "ocr_json",
    "scanned",
    "approved",
    "matched",
}
_LEGACY_MIGRATION_MARKER = ".beanbeaver_migrated.json"


def _project_paths():
    return get_paths()


def _receipts_root() -> Path:
    return _project_paths().receipts


def _legacy_scanned_dir() -> Path:
    return _project_paths().receipts_json_scanned


def _legacy_approved_dir() -> Path:
    return _project_paths().receipts_json_approved


def _legacy_matched_dir() -> Path:
    return _project_paths().receipts_json_matched


def _legacy_rendered_scanned_dir() -> Path:
    return _project_paths().receipts_rendered_scanned


def _legacy_rendered_approved_dir() -> Path:
    return _project_paths().receipts_rendered_approved


def _legacy_rendered_matched_dir() -> Path:
    return _project_paths().receipts_rendered_matched


def _legacy_flat_scanned_dir() -> Path:
    return _project_paths().receipts / "scanned"


def _legacy_flat_approved_dir() -> Path:
    return _project_paths().receipts / "approved"


def _legacy_flat_matched_dir() -> Path:
    return _project_paths().receipts / "matched"


def ensure_directories() -> None:
    """Create required receipt directories if they do not exist."""
    _project_paths().ensure_receipt_directories()
    _migrate_legacy_flat_receipts()
    _migrate_legacy_status_tree_receipts()


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


def _next_available_file(path: Path) -> Path:
    """Return a unique file path when collisions exist."""
    if not path.exists():
        return path

    counter = 1
    stem = path.stem
    suffix = path.suffix
    while True:
        candidate = path.parent / f"{stem}_{counter}{suffix}"
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


def _legacy_migration_marker_path(receipt_dir: Path) -> Path:
    return receipt_dir / _LEGACY_MIGRATION_MARKER


def _ensure_receipt_dir(receipt_dir: Path) -> None:
    receipt_dir.mkdir(parents=True, exist_ok=True)
    _source_dir(receipt_dir).mkdir(parents=True, exist_ok=True)
    _ocr_dir(receipt_dir).mkdir(parents=True, exist_ok=True)
    _stages_dir(receipt_dir).mkdir(parents=True, exist_ok=True)
    _rendered_dir(receipt_dir).mkdir(parents=True, exist_ok=True)


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


def _rendered_filename(document: dict[str, Any]) -> str:
    """Return the legacy rendered Beancount filename for a stage document."""
    return f"{_receipt_dir_name(document)}.beancount"


def _legacy_flat_receipt_mappings() -> tuple[tuple[Path, str], ...]:
    """Return legacy flat-file receipt roots paired with semantic status."""
    return (
        (_legacy_flat_scanned_dir(), "scanned"),
        (_legacy_flat_approved_dir(), "approved"),
        (_legacy_flat_matched_dir(), "matched"),
    )


def _legacy_status_tree_mappings() -> tuple[tuple[Path, Path, str], ...]:
    """Return legacy status-tree JSON roots with their rendered roots and status."""
    return (
        (_legacy_scanned_dir(), _legacy_rendered_scanned_dir(), "scanned"),
        (_legacy_approved_dir(), _legacy_rendered_approved_dir(), "approved"),
        (_legacy_matched_dir(), _legacy_rendered_matched_dir(), "matched"),
    )


def _parse_legacy_receipt_from_beancount(filepath: Path) -> tuple[Receipt, str | None]:
    """Reconstruct a Receipt and metadata from a legacy flat Beancount file."""
    content = filepath.read_text(encoding="utf-8")
    lines = content.splitlines()

    merchant = "Unknown"
    receipt_date: date | None = None
    date_is_unknown = False
    total = Decimal("0")
    items = []
    tax: Decimal | None = None
    image_filename = ""
    image_sha256: str | None = None

    raw_text_lines: list[str] = []
    in_raw_text = False

    for line in lines:
        stripped = line.strip()
        if stripped == "; --- Raw OCR Text (for reference) ---":
            in_raw_text = True
            continue
        if in_raw_text:
            if stripped.startswith(";"):
                raw_line = stripped[1:]
                if raw_line.startswith(" "):
                    raw_line = raw_line[1:]
                raw_text_lines.append(raw_line)
                continue
            if not stripped:
                continue
            in_raw_text = False

        if stripped.startswith("; @merchant:"):
            merchant = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("; @date:"):
            date_value = stripped.split(":", 1)[1].strip()
            if date_value.upper() == "UNKNOWN":
                date_is_unknown = True
                receipt_date = None
            else:
                try:
                    receipt_date = date.fromisoformat(date_value)
                except ValueError:
                    receipt_date = None
        elif stripped.startswith("; @total:"):
            try:
                total = Decimal(stripped.split(":", 1)[1].strip())
            except InvalidOperation:
                pass
        elif stripped.startswith("; @tax:"):
            try:
                tax = Decimal(stripped.split(":", 1)[1].strip())
            except InvalidOperation:
                pass
        elif stripped.startswith("; @image_filename:"):
            image_filename = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("; @image:"):
            image_filename = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("; @image_sha256:"):
            candidate = stripped.split(":", 1)[1].strip()
            image_sha256 = candidate or None

    for line in lines:
        stripped = line.strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}\s+\S", stripped):
            if receipt_date is None or date_is_unknown:
                try:
                    receipt_date = date.fromisoformat(stripped[:10])
                    date_is_unknown = False
                except ValueError:
                    pass
            payee_match = re.search(r'\S+\s+"([^"]*)"', stripped)
            if payee_match and merchant == "Unknown":
                merchant = payee_match.group(1)
            break

    expense_pattern = re.compile(r"^\s+(Expenses:\S+)\s+([+-]?\d+(?:\.\d+)?)\s+\w+\s*;?\s*(.*)$")
    for line in lines:
        match = expense_pattern.match(line)
        if not match:
            continue

        category = match.group(1)
        try:
            price = Decimal(match.group(2))
        except InvalidOperation:
            continue

        description = match.group(3).strip()
        if "Tax:HST" in category or "Tax:GST" in category:
            tax = price
            continue
        if "FIXME: unaccounted" in description:
            continue

        quantity = 1
        qty_match = re.search(r"\(qty\s+(\d+)\)", description)
        if qty_match:
            quantity = int(qty_match.group(1))
            description = re.sub(r"\s*\(qty\s+\d+\)", "", description)

        items.append(
            {
                "description": description,
                "price": price,
                "quantity": quantity,
                "category": category,
            }
        )

    if total == Decimal("0") and items:
        total = sum((item["price"] for item in items), Decimal("0"))
        if tax:
            total += tax

    date_is_placeholder = date_is_unknown
    if receipt_date is None:
        receipt_date = placeholder_receipt_date()
        date_is_placeholder = True

    receipt = Receipt(
        merchant=merchant,
        date=receipt_date,
        date_is_placeholder=date_is_placeholder,
        total=total,
        items=[
            ReceiptItem(
                description=item["description"],
                price=item["price"],
                quantity=item["quantity"],
                category=item["category"],
            )
            for item in items
        ],
        tax=tax,
        raw_text="\n".join(raw_text_lines),
        image_filename=image_filename,
    )
    return receipt, image_sha256


def _documents_for_status(document: dict[str, Any], *, status: str, pass_name: str) -> list[dict[str, Any]]:
    """Expand one base parsed document into the latest required status chain."""
    documents = [document]
    parent_file = _canonical_stage_filename(document)

    if status in {"approved", "matched"} and _stage_status(documents[-1]) == "scanned":
        review_document = clone_stage_document(
            documents[-1],
            stage="review_stage_1",
            created_by="storage_migration",
            pass_name=pass_name,
            parent_file=parent_file,
        )
        documents.append(review_document)
        parent_file = _canonical_stage_filename(review_document)

    if status == "matched" and _stage_status(documents[-1]) != "matched":
        matched_document = clone_stage_document(
            documents[-1],
            stage="matched",
            created_by="storage_migration",
            pass_name=pass_name,
            parent_file=parent_file,
        )
        documents.append(matched_document)

    return documents


def _write_canonical_receipt_chain(
    documents: list[dict[str, Any]],
    *,
    rendered_content: str | None = None,
) -> Path:
    """Persist one canonical receipt chain and return its latest stage path."""
    latest_document = documents[-1]
    receipt_dir = _next_available_dir(_receipts_root() / _receipt_dir_name(latest_document))
    _ensure_receipt_dir(receipt_dir)

    latest_stage_path: Path | None = None
    for document in documents:
        stage_path = _stages_dir(receipt_dir) / _canonical_stage_filename(document)
        save_stage_document(stage_path, document)
        latest_stage_path = stage_path

    if latest_stage_path is None:
        raise ValueError("No stage documents to save")

    current_path, rendered_path = _write_current_artifacts(receipt_dir, latest_stage_path)
    if rendered_content is not None:
        rendered_path.write_text(rendered_content, encoding="utf-8")
    logger.debug("Wrote receipt chain to %s (current: %s)", receipt_dir, current_path)
    return latest_stage_path


def _migrate_legacy_flat_receipt(legacy_path: Path, *, status: str) -> None:
    """Convert one legacy flat receipt file into the canonical staged layout."""
    receipt, image_sha256 = _parse_legacy_receipt_from_beancount(legacy_path)
    document = build_parsed_receipt_stage(
        receipt,
        rule_layers=load_receipt_structuring_rule_layers(),
        image_sha256=image_sha256,
        created_by="legacy_migration",
        pass_name=f"legacy_flat_{status}",
    )
    document["meta"]["receipt_id"] = hashlib.sha256(legacy_path.read_bytes()).hexdigest()
    document["meta"]["legacy_source_path"] = str(legacy_path.relative_to(_project_paths().root))

    for item_doc, item in zip(document.get("items") or [], receipt.items):
        if item.category:
            classification = dict(item_doc.get("classification") or {})
            classification["category"] = item.category
            item_doc["classification"] = classification

    documents = _documents_for_status(document, status=status, pass_name=f"legacy_flat_{status}")
    _write_canonical_receipt_chain(
        documents,
        rendered_content=legacy_path.read_text(encoding="utf-8"),
    )
    legacy_path.unlink()
    logger.info("Migrated legacy %s receipt %s into canonical storage", status, legacy_path)


def _migrate_legacy_flat_receipts() -> None:
    """Move legacy flat receipt files into the canonical staged layout."""
    for legacy_root, status in _legacy_flat_receipt_mappings():
        if not legacy_root.exists():
            continue
        for legacy_path in sorted(legacy_root.glob("*.beancount")):
            try:
                _migrate_legacy_flat_receipt(legacy_path, status=status)
            except Exception as exc:
                logger.warning("Failed to migrate legacy %s receipt %s: %s", status, legacy_path, exc)


def _migrate_legacy_stage_chain(receipt_dir: Path, *, rendered_root: Path, status: str) -> Path:
    """Convert one legacy status-tree receipt directory into canonical storage."""
    stage_paths = sorted(receipt_dir.glob("*.receipt.json"), key=lambda path: get_stage_index(load_stage_document(path)))
    if not stage_paths:
        raise FileNotFoundError(f"No stage JSON files found in {receipt_dir}")

    documents = [load_stage_document(path) for path in stage_paths]
    documents = _documents_for_status(documents[-1], status=status, pass_name=f"legacy_tree_{status}") \
        if len(documents) == 1 and _stage_status(documents[-1]) != status \
        else list(documents)

    if status == "matched" and _stage_status(documents[-1]) != "matched":
        documents = list(documents)
        documents.append(
            clone_stage_document(
                documents[-1],
                stage="matched",
                created_by="storage_migration",
                pass_name="legacy_tree_matched",
                parent_file=_canonical_stage_filename(documents[-1]),
            )
        )

    rendered_path = rendered_root / f"{receipt_dir.name}.beancount"
    rendered_content = rendered_path.read_text(encoding="utf-8") if rendered_path.exists() else None
    latest_stage_path = _write_canonical_receipt_chain(documents, rendered_content=rendered_content)
    canonical_receipt_dir = _receipt_dir_for_stage_path(latest_stage_path)
    stage_index_map = {
        str(get_stage_index(load_stage_document(path))): str(
            (_stages_dir(canonical_receipt_dir) / _canonical_stage_filename(load_stage_document(path))).relative_to(
                canonical_receipt_dir
            )
        )
        for path in stage_paths
    }
    _legacy_migration_marker_path(receipt_dir).write_text(
        json.dumps(
            {
                "canonical_receipt_dir": str(canonical_receipt_dir),
                "latest_stage_path": str(latest_stage_path),
                "status": status,
                "stage_index_map": stage_index_map,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    logger.info("Migrated legacy %s receipt dir %s -> %s", status, receipt_dir, latest_stage_path)
    return latest_stage_path


def _migrate_legacy_status_tree_receipts() -> None:
    """Move legacy json/{status} receipt directories into canonical receipt directories."""
    for legacy_root, rendered_root, status in _legacy_status_tree_mappings():
        if not legacy_root.exists():
            continue
        for receipt_dir in sorted(path for path in legacy_root.iterdir() if path.is_dir()):
            if _legacy_migration_marker_path(receipt_dir).exists():
                continue
            try:
                _migrate_legacy_stage_chain(receipt_dir, rendered_root=rendered_root, status=status)
            except Exception as exc:
                logger.warning("Failed to migrate legacy %s receipt dir %s: %s", status, receipt_dir, exc)


def _is_canonical_receipt_dir(path: Path) -> bool:
    return path.parent == _receipts_root() and path.name not in _LEGACY_RECEIPT_DIR_NAMES


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
    """Accept legacy input paths and return the canonical stage path."""
    path = stage_path.resolve()
    if not path.exists():
        return path

    if path.name == "current.receipt.json" and _is_canonical_receipt_dir(path.parent):
        return _latest_stage_path(path.parent)
    if path.parent.name == _project_paths().receipts_stages_dirname and _is_canonical_receipt_dir(path.parent.parent):
        return path

    for legacy_root, rendered_root, status in _legacy_status_tree_mappings():
        try:
            path.relative_to(legacy_root.resolve())
        except ValueError:
            continue

        marker_path = _legacy_migration_marker_path(path.parent)
        if marker_path.exists():
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                marker = {}

            canonical_receipt_dir_raw = marker.get("canonical_receipt_dir")
            stage_index_map = marker.get("stage_index_map")
            if isinstance(canonical_receipt_dir_raw, str) and isinstance(stage_index_map, dict):
                canonical_receipt_dir = Path(canonical_receipt_dir_raw)
                stage_index = str(get_stage_index(load_stage_document(path)))
                relative_stage_path = stage_index_map.get(stage_index)
                if isinstance(relative_stage_path, str):
                    candidate = canonical_receipt_dir / relative_stage_path
                    if candidate.exists():
                        return candidate

        stage_index = get_stage_index(load_stage_document(path))
        latest_stage_path = _migrate_legacy_stage_chain(path.parent, rendered_root=rendered_root, status=status)
        receipt_dir = _receipt_dir_for_stage_path(latest_stage_path)
        for candidate in _stage_files(receipt_dir):
            if get_stage_index(load_stage_document(candidate)) == stage_index:
                return candidate
        return latest_stage_path

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
    return sorted(
        path for path in _receipts_root().iterdir() if path.is_dir() and path.name not in _LEGACY_RECEIPT_DIR_NAMES
    )


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
        rule_layers=load_receipt_structuring_rule_layers(),
        raw_ocr_payload=raw_ocr_payload,
        ocr_json_path=str(ocr_json_path.relative_to(_project_paths().receipts)) if ocr_json_path else None,
        image_sha256=image_sha256,
    )
    latest_stage_path = _write_canonical_receipt_chain([document])
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
