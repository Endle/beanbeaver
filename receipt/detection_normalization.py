"""Post-OCR detection normalization pipeline.

LLVM-inspired sequence of detection passes operating on ``list[Detection]``.
Each op is a pure ``(detections, context) -> detections`` function so passes
can be reordered, swapped, or unit-tested in isolation. Mirrors the pre-OCR
image pipeline but at the bbox layer.

Default ordering: filter_low_quality -> filter_bob_markers ->
deskew_detections -> sort_reading_order.

The numeric work (quality/marker filtering, RANSAC deskew, shear, reading-order
sort) lives in the native ``beanbeaver._rust_matcher`` extension; this module is
a thin orchestration layer that keeps the op-callable API stable, marshals
detection dicts, and owns the optional debug-dump filesystem I/O.

When ``BEANBEAVER_POSTOCR_DUMP_DIR`` points to a parent directory, every
``normalize_detections`` call writes a per-call subdir containing the input
detections, each pass's output, and a ``trace.json`` of per-op metadata
(deskew angle, kept/dropped counts, etc).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._rust import require_rust_matcher

Detection = dict[str, Any]

POSTOCR_DUMP_DIR_ENV = "BEANBEAVER_POSTOCR_DUMP_DIR"

# Pipeline thresholds are owned by the Rust implementation and re-exported here
# so callers and tests keep importing them from this module unchanged.
_CONSTANTS = require_rust_matcher().detection_constants()
MIN_CONFIDENCE: float = _CONSTANTS["MIN_CONFIDENCE"]
MIN_TEXT_LENGTH: int = _CONSTANTS["MIN_TEXT_LENGTH"]
DESKEW_MIN_CONFIDENCE: float = _CONSTANTS["DESKEW_MIN_CONFIDENCE"]
DESKEW_MIN_ITEM_WIDTH: float = _CONSTANTS["DESKEW_MIN_ITEM_WIDTH"]
DESKEW_MIN_PRICE_WIDTH: float = _CONSTANTS["DESKEW_MIN_PRICE_WIDTH"]
DESKEW_MIN_X_DISTANCE: float = _CONSTANTS["DESKEW_MIN_X_DISTANCE"]
DESKEW_ITEM_X_MAX_FRAC: float = _CONSTANTS["DESKEW_ITEM_X_MAX_FRAC"]
DESKEW_PRICE_X_MIN_FRAC: float = _CONSTANTS["DESKEW_PRICE_X_MIN_FRAC"]
DESKEW_Y_WINDOW_PX: int = _CONSTANTS["DESKEW_Y_WINDOW_PX"]
DESKEW_ANGLE_CAP_DEG: float = _CONSTANTS["DESKEW_ANGLE_CAP_DEG"]
DESKEW_MIN_ANGLE_DEG: float = _CONSTANTS["DESKEW_MIN_ANGLE_DEG"]
DESKEW_INLIER_TOL_DEG: float = _CONSTANTS["DESKEW_INLIER_TOL_DEG"]
DESKEW_MIN_INLIERS: int = _CONSTANTS["DESKEW_MIN_INLIERS"]
DESKEW_MIN_CONSENSUS: float = _CONSTANTS["DESKEW_MIN_CONSENSUS"]
DESKEW_RANSAC_ITERS: int = _CONSTANTS["DESKEW_RANSAC_ITERS"]
DESKEW_RANSAC_SEED: int = _CONSTANTS["DESKEW_RANSAC_SEED"]


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


def filter_low_quality_op(
    detections: list[Detection],
    ctx: DetectionNormalizationContext,
) -> list[Detection]:
    """Drop detections below the confidence floor or with too-short text."""
    kept_indices = require_rust_matcher().detection_filter_low_quality(detections)
    kept = [detections[index] for index in kept_indices]
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
    kept_indices = require_rust_matcher().detection_filter_bob_markers(detections)
    kept = [detections[index] for index in kept_indices]
    ctx.trace.append({"op": "filter_bob_markers", "input": len(detections), "output": len(kept)})
    return kept


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
    record, new_y = require_rust_matcher().detection_deskew(detections, ctx.image_width)
    ctx.trace.append(record)
    if new_y is None:
        return detections
    return [
        {**det, "center_y": center_y, "y_min": y_min, "y_max": y_max}
        for det, (center_y, y_min, y_max) in zip(detections, new_y, strict=True)
    ]


def sort_reading_order_op(
    detections: list[Detection],
    ctx: DetectionNormalizationContext,
) -> list[Detection]:
    """Sort by (center_y, min_x) for top-to-bottom, left-to-right order."""
    order = require_rust_matcher().detection_sort_reading_order(detections)
    out = [detections[index] for index in order]
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
