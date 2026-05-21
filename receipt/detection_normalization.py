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
import random
import re
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Detection = dict[str, Any]

POSTOCR_DUMP_DIR_ENV = "BEANBEAVER_POSTOCR_DUMP_DIR"

MIN_CONFIDENCE = 0.7
MIN_TEXT_LENGTH = 2

# Detection-level deskew via RANSAC over same-row item↔price slopes.
# See docs/detection_deskew_plan.md for derivation.
DESKEW_MIN_CONFIDENCE = 0.95
DESKEW_MIN_ITEM_WIDTH = 0.08  # × image_width
DESKEW_MIN_PRICE_WIDTH = 0.03
DESKEW_MIN_X_DISTANCE = 0.50
DESKEW_ITEM_X_MAX_FRAC = 0.40
DESKEW_PRICE_X_MIN_FRAC = 0.60
DESKEW_Y_WINDOW_PX = 200
DESKEW_ANGLE_CAP_DEG = 5.0
DESKEW_MIN_ANGLE_DEG = 0.3
DESKEW_INLIER_TOL_DEG = 0.2
DESKEW_MIN_INLIERS = 5
DESKEW_MIN_CONSENSUS = 0.60
DESKEW_RANSAC_ITERS = 50
DESKEW_RANSAC_SEED = 0

_PRICE_TEXT_RE = re.compile(r"^\s*[-$]?\d+\.\d{2}[A-Z]?\s*$")


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


@dataclass(frozen=True)
class _PairCandidate:
    """A single (item bbox, price bbox) candidate with its implied tilt angle.

    Same-row item↔price slopes are used (not individual bbox top/centerline
    angles) because both sides have matched glyph patterns — the asymmetry
    bias that derailed the prior centerline-median estimator cancels out.
    """

    item_center_y: float
    price_center_y: float
    item_center_x: float
    price_center_x: float
    angle_deg: float


def _bbox_x_extent(bbox: list[list[float]]) -> tuple[float, float, float]:
    xs = [p[0] for p in bbox]
    return min(xs), max(xs), sum(xs) / len(xs)


def _build_pair_candidates(detections: list[Detection], image_width: int) -> list[_PairCandidate]:
    """Cross-product item/price candidates filtered by column/width/proximity.

    Liberally generated; mispairings are expected to fall out as RANSAC
    outliers rather than being filtered upfront (tightening here would
    re-introduce a chicken-and-egg dependency on the matcher).
    """
    item_x_max_cap = image_width * DESKEW_ITEM_X_MAX_FRAC
    price_x_min_floor = image_width * DESKEW_PRICE_X_MIN_FRAC
    min_item_width = image_width * DESKEW_MIN_ITEM_WIDTH
    min_price_width = image_width * DESKEW_MIN_PRICE_WIDTH
    min_x_distance = image_width * DESKEW_MIN_X_DISTANCE

    items: list[tuple[float, float, float]] = []  # (cx, cy, x_max)
    prices: list[tuple[float, float, float]] = []  # (cx, cy, x_min)

    for det in detections:
        if float(det.get("confidence", 0.0)) < DESKEW_MIN_CONFIDENCE:
            continue
        bbox = det.get("bbox") or []
        if len(bbox) < 4:
            continue
        x_min, x_max, x_center = _bbox_x_extent(bbox)
        width = x_max - x_min
        if width <= 0:
            continue
        cy = float(det["center_y"])
        text = (det.get("text") or "").strip()

        if x_max < item_x_max_cap and width >= min_item_width:
            items.append((x_center, cy, x_max))
        if x_min > price_x_min_floor and width >= min_price_width and _PRICE_TEXT_RE.match(text):
            prices.append((x_center, cy, x_min))

    candidates: list[_PairCandidate] = []
    for icx, icy, _ in items:
        for pcx, pcy, _ in prices:
            dx = pcx - icx
            if dx < min_x_distance:
                continue
            if abs(pcy - icy) > DESKEW_Y_WINDOW_PX:
                continue
            angle = math.degrees(math.atan2(pcy - icy, dx))
            candidates.append(
                _PairCandidate(
                    item_center_y=icy,
                    price_center_y=pcy,
                    item_center_x=icx,
                    price_center_x=pcx,
                    angle_deg=angle,
                )
            )
    return candidates


def _ransac_consensus(candidates: list[_PairCandidate]) -> tuple[float, int]:
    """Return (best_angle_deg, inlier_count). 0.0/0 when no consensus found.

    Deterministic via DESKEW_RANSAC_SEED — same input always produces the
    same output, matching the rest of the pipeline's reproducibility story.
    """
    if len(candidates) < 3:
        return 0.0, 0
    rng = random.Random(DESKEW_RANSAC_SEED)
    best_angle = 0.0
    best_inliers = 0
    for _ in range(DESKEW_RANSAC_ITERS):
        sample = rng.sample(candidates, k=3)
        trial = statistics.median(c.angle_deg for c in sample)
        if abs(trial) > DESKEW_ANGLE_CAP_DEG:
            continue
        inliers = [c for c in candidates if abs(c.angle_deg - trial) <= DESKEW_INLIER_TOL_DEG]
        if len(inliers) > best_inliers:
            best_inliers = len(inliers)
            best_angle = statistics.fmean(c.angle_deg for c in inliers)
    return best_angle, best_inliers


def _apply_shear(detections: list[Detection], angle_deg: float, image_width: int) -> list[Detection]:
    tan_angle = math.tan(math.radians(angle_deg))
    x_ref = image_width / 2
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
    return corrected


def deskew_detections_op(
    detections: list[Detection],
    ctx: DetectionNormalizationContext,
) -> list[Detection]:
    """Vertical shear correction driven by same-row item↔price slopes.

    Builds candidate pairs from high-confidence detections in the left/right
    columns, runs RANSAC on their implied angles, and shears y-coordinates
    only when consensus is strong, the angle is in band, and large enough to
    matter. Bias is "miss safely" — a wrong correction can push borderline
    rows out of the matcher's y-band, the failure mode that killed the prior
    centerline-median estimator.
    """
    candidates = _build_pair_candidates(detections, ctx.image_width)
    angle, inliers = _ransac_consensus(candidates)
    consensus_ratio = inliers / len(candidates) if candidates else 0.0
    record: dict[str, Any] = {
        "op": "deskew_detections",
        "angle_deg": angle,
        "applied": False,
        "candidate_count": len(candidates),
        "inlier_count": inliers,
        "consensus_ratio": consensus_ratio,
        "gate_reason": None,
    }

    gate_reason: str | None = None
    if not candidates:
        gate_reason = "no_candidates"
    elif inliers < DESKEW_MIN_INLIERS:
        gate_reason = "too_few_inliers"
    elif abs(angle) > DESKEW_ANGLE_CAP_DEG:
        gate_reason = "angle_too_large"
    elif consensus_ratio < DESKEW_MIN_CONSENSUS:
        gate_reason = "weak_consensus"
    elif abs(angle) < DESKEW_MIN_ANGLE_DEG:
        gate_reason = "angle_too_small"

    if gate_reason is not None:
        record["gate_reason"] = gate_reason
        ctx.trace.append(record)
        return detections

    record["applied"] = True
    ctx.trace.append(record)
    return _apply_shear(detections, angle, ctx.image_width)


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

    ``deskew_detections_op`` runs between marker filtering and the reading-
    order sort so the sort sees post-shear y-coordinates. Its gating is
    aggressive: it should no-op on already-straight receipts and only fire
    on genuinely tilted inputs. See docs/detection_deskew_plan.md.
    """
    return [
        filter_low_quality_op,
        filter_bob_markers_op,
        deskew_detections_op,
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
        (resolved_dir / "detections_input.json").write_text(json.dumps(detections, indent=2, default=str) + "\n")

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
        (resolved_dir / "trace.json").write_text(json.dumps(ctx.trace, indent=2, default=str) + "\n")

    return normalized
