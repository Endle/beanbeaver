"""Post-OCR detection normalization pipeline.

LLVM-inspired sequence of detection passes operating on ``list[Detection]``.
Each op is a pure ``(detections, context) -> detections`` function so passes
can be reordered, swapped, or unit-tested in isolation. Mirrors the pre-OCR
image pipeline but at the bbox layer.

Default ordering: filter_low_quality -> filter_bob_markers ->
deskew_detections -> sort_reading_order.

When ``BEANBEAVER_POSTOCR_DUMP_DIR`` points to a parent directory, every
``normalize_detections`` call writes a per-call subdir containing the input
detections, each pass's output, and a ``trace.json`` of per-op metadata
(deskew angle, kept/dropped counts, etc).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Detection = dict[str, Any]

POSTOCR_DUMP_DIR_ENV = "BEANBEAVER_POSTOCR_DUMP_DIR"

MIN_CONFIDENCE = 0.7
MIN_TEXT_LENGTH = 2

DESKEW_MIN_CONFIDENCE = 0.9
DESKEW_MIN_WIDTH_RATIO = 0.05
DESKEW_MIN_ASPECT_RATIO = 2.0
DESKEW_MIN_ANGLE_DEG = 0.1


@dataclass
class DetectionNormalizationContext:
    """Execution context shared by detection normalization operations.

    ``trace`` is appended to by each op for per-call diagnostics.
    ``debug_dir``, when set, receives JSON snapshots per pass.
    """

    image_width: int
    image_height: int
    merchant_hint: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    debug_dir: Path | None = None


DetectionNormalizationOp = Callable[[list[Detection], DetectionNormalizationContext], list[Detection]]


def _boxes_overlap_y(det1: Detection, det2: Detection, min_overlap_ratio: float = 0.3) -> bool:
    """Check vertical overlap between two detections."""
    y1_min, y1_max = det1["y_min"], det1["y_max"]
    y2_min, y2_max = det2["y_min"], det2["y_max"]
    overlap_start = max(y1_min, y2_min)
    overlap_end = min(y1_max, y2_max)
    if overlap_start >= overlap_end:
        return False
    overlap = overlap_end - overlap_start
    smaller_height = min(y1_max - y1_min, y2_max - y2_min)
    if smaller_height <= 0:
        return False
    return overlap / smaller_height >= min_overlap_ratio


def _is_bob_marker_text(text: str) -> bool:
    """Return True for Costco Bottom-Of-Basket marker rows."""
    upper = text.upper()
    has_bottom_banner = "BOTTOM OF BAS" in upper
    has_bob_count_marker = "BOB COUNT" in upper and bool(re.search(r"[X*]{4,}", upper))
    return has_bottom_banner or has_bob_count_marker


def filter_low_quality_op(
    detections: list[Detection],
    ctx: DetectionNormalizationContext,
) -> list[Detection]:
    """Drop detections below the confidence floor or with too-short text."""
    kept: list[Detection] = []
    for det in detections:
        if float(det.get("confidence", 0.0)) < MIN_CONFIDENCE:
            continue
        if len((det.get("text") or "").strip()) < MIN_TEXT_LENGTH:
            continue
        kept.append(det)
    ctx.trace.append({"op": "filter_low_quality", "input": len(detections), "output": len(kept)})
    return kept


def filter_bob_markers_op(
    detections: list[Detection],
    ctx: DetectionNormalizationContext,
) -> list[Detection]:
    """Drop Costco BOB markers that overlap real item rows.

    BOB marker rows ('*xxxBottom of Bas...', '*xBOB Count N') can occupy the
    same Y band as a real item+price pair on Costco receipts. Keeping them
    hijacks line grouping; drop only when they overlap a non-marker.
    """
    if not detections:
        ctx.trace.append({"op": "filter_bob_markers", "input": 0, "output": 0})
        return detections
    filtered: list[Detection] = []
    for det in detections:
        if not _is_bob_marker_text(det["text"]):
            filtered.append(det)
            continue
        overlaps_non_marker = any(
            other is not det
            and not _is_bob_marker_text(other["text"])
            and _boxes_overlap_y(det, other, min_overlap_ratio=0.25)
            for other in detections
        )
        if not overlaps_non_marker:
            filtered.append(det)
    ctx.trace.append({"op": "filter_bob_markers", "input": len(detections), "output": len(filtered)})
    return filtered


def _estimate_page_skew(detections: list[Detection], image_width: int) -> tuple[float, int]:
    """Width-weighted median angle (degrees) of qualifying bbox centerlines.

    Uses the centerline (left-edge midpoint -> right-edge midpoint) rather
    than just the top edge: this cancels out asymmetric ascender/descender
    contributions and other top/bottom-only biases in PaddleOCR's polygon
    output.

    Filters: conf >= 0.9, width >= 5% of image_width, aspect ratio >= 2.
    A horizontal text row has angle 0; clockwise tilt yields positive angle.
    """
    samples: list[tuple[float, float]] = []
    min_width = image_width * DESKEW_MIN_WIDTH_RATIO
    for det in detections:
        if float(det.get("confidence", 0.0)) < DESKEW_MIN_CONFIDENCE:
            continue
        bbox = det.get("bbox") or []
        if len(bbox) < 4:
            continue
        # Centerline: midpoint of left edge (TL,BL) to midpoint of right edge (TR,BR).
        lx = (bbox[0][0] + bbox[3][0]) / 2
        ly = (bbox[0][1] + bbox[3][1]) / 2
        rx = (bbox[1][0] + bbox[2][0]) / 2
        ry = (bbox[1][1] + bbox[2][1]) / 2
        dx = rx - lx
        if dx <= 0:
            continue
        width = abs(dx)
        if width < min_width:
            continue
        height = abs(det["y_max"] - det["y_min"])
        if height <= 0 or width / height < DESKEW_MIN_ASPECT_RATIO:
            continue
        angle = math.degrees(math.atan2(ry - ly, dx))
        samples.append((angle, width))
    if not samples:
        return 0.0, 0
    samples.sort(key=lambda s: s[0])
    total_weight = sum(w for _, w in samples)
    half = total_weight / 2
    acc = 0.0
    for angle, weight in samples:
        acc += weight
        if acc >= half:
            return angle, len(samples)
    return samples[-1][0], len(samples)


def deskew_detections_op(
    detections: list[Detection],
    ctx: DetectionNormalizationContext,
) -> list[Detection]:
    """Vertical shear correction using the bbox-derived page skew angle.

    Computes a global angle from qualifying bboxes, then shifts each
    detection's y-coordinates by ``-(x_center - image_width/2) * tan(angle)``.
    No-op below 0.1 deg or when no qualifying samples exist. Image bytes and
    glyph recognition are untouched: this only re-aligns y for row grouping.
    """
    angle, sample_count = _estimate_page_skew(detections, ctx.image_width)
    record: dict[str, Any] = {
        "op": "deskew_detections",
        "angle_deg": angle,
        "sample_count": sample_count,
        "applied": False,
    }
    if abs(angle) < DESKEW_MIN_ANGLE_DEG or sample_count == 0:
        ctx.trace.append(record)
        return detections

    record["applied"] = True
    tan_angle = math.tan(math.radians(angle))
    x_ref = ctx.image_width / 2
    corrected: list[Detection] = []
    for det in detections:
        bbox = det["bbox"]
        x_center = sum(p[0] for p in bbox) / len(bbox)
        delta = (x_center - x_ref) * tan_angle
        new_det = dict(det)
        new_det["center_y"] = det["center_y"] - delta
        new_det["y_min"] = det["y_min"] - delta
        new_det["y_max"] = det["y_max"] - delta
        corrected.append(new_det)
    ctx.trace.append(record)
    return corrected


def sort_reading_order_op(
    detections: list[Detection],
    ctx: DetectionNormalizationContext,
) -> list[Detection]:
    """Sort by (center_y, min_x) for top-to-bottom, left-to-right order."""
    out = sorted(detections, key=lambda d: (d["center_y"], d["min_x"]))
    ctx.trace.append({"op": "sort_reading_order", "count": len(out)})
    return out


def default_detection_pipeline() -> list[DetectionNormalizationOp]:
    """Default post-OCR ops in execution order.

    ``deskew_detections_op`` is intentionally NOT included. Validated on the
    39-case private corpus: it regressed 5 receipts (jinlian, foody mart,
    bestco x2, fresh) and improved zero. Root cause: estimating page skew
    from bbox top/centerline angles has more confounding biases than expected
    -- PaddleOCR's polygon shape is influenced by glyph composition (prices
    starting with "$" pull the bbox top up; items with descenders pull the
    bottom down), creating a systematic angle signature that looks like real
    tilt but isn't. On a jinlian SHRIMP PASTE row, the detector picked +0.31
    deg clockwise while the actual item-to-price slope was -0.83 deg counter-
    clockwise -- opposite sign. Reliable bbox-based deskew likely needs pairs
    of bboxes known to be on the same logical row, which is what the matcher
    itself produces (chicken-and-egg). The op is preserved for opt-in use and
    future re-evaluation.
    """
    return [
        filter_low_quality_op,
        filter_bob_markers_op,
        sort_reading_order_op,
    ]


def _resolve_dump_dir(detections: list[Detection], explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    root = os.environ.get(POSTOCR_DUMP_DIR_ENV)
    if not root:
        return None
    first_text = detections[0].get("text", "") if detections else ""
    seed = f"{len(detections)}:{first_text}".encode()
    digest = hashlib.sha1(seed).hexdigest()[:8]
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    return Path(root) / f"{timestamp}_{digest}"


def normalize_detections(
    detections: list[Detection],
    *,
    image_width: int,
    image_height: int,
    merchant_hint: str | None = None,
    operations: Sequence[DetectionNormalizationOp] | None = None,
    debug_dir: Path | None = None,
) -> list[Detection]:
    """Run detection normalization operations in sequence.

    When ``operations`` is omitted, returns a shallow copy unchanged.
    """
    resolved_dir = _resolve_dump_dir(detections, debug_dir)
    if resolved_dir is not None:
        resolved_dir.mkdir(parents=True, exist_ok=True)
        (resolved_dir / "detections_input.json").write_text(
            json.dumps(detections, indent=2, default=str) + "\n"
        )

    ctx = DetectionNormalizationContext(
        image_width=image_width,
        image_height=image_height,
        merchant_hint=merchant_hint,
        debug_dir=resolved_dir,
    )
    normalized = list(detections)
    for index, operation in enumerate(operations or ()):
        normalized = operation(normalized, ctx)
        if resolved_dir is not None:
            name = operation.__name__
            (resolved_dir / f"detections_{index:02d}_{name}.json").write_text(
                json.dumps(normalized, indent=2, default=str) + "\n"
            )

    if resolved_dir is not None:
        (resolved_dir / "trace.json").write_text(
            json.dumps(ctx.trace, indent=2, default=str) + "\n"
        )

    return normalized
