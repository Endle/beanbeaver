"""Pure OCR transformation helpers for receipt parsing."""

import hashlib
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from PIL import Image

from .detection_normalization import default_detection_pipeline, normalize_detections
from .image_pipeline import (
    MAX_IMAGE_DIMENSION,
    OCR_IMAGE_PADDING,
    ImagePipelineContext,
    default_image_pipeline,
    run_image_pipeline,
)
from .ocr_schema import OCR_ENGINE_NAME_PADDLE, OCR_SCHEMA_VERSION, OcrBBox, OcrDocument

PREOCR_DUMP_DIR_ENV = "BEANBEAVER_PREOCR_DUMP_DIR"

__all__ = [
    "MAX_IMAGE_DIMENSION",
    "OCR_IMAGE_PADDING",
    "PREOCR_DUMP_DIR_ENV",
    "resize_image_bytes",
    "transform_paddleocr_result",
]


def _resolve_dump_dir(image_bytes: bytes, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    root = os.environ.get(PREOCR_DUMP_DIR_ENV)
    if not root:
        return None
    digest = hashlib.sha1(image_bytes).hexdigest()[:8]
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    return Path(root) / f"{timestamp}_{digest}"


def resize_image_bytes(
    image_bytes: bytes,
    max_dimension: int = MAX_IMAGE_DIMENSION,
    padding: int = OCR_IMAGE_PADDING,
    *,
    debug_dir: Path | None = None,
) -> bytes:
    """Run the pre-OCR image pipeline on ``image_bytes`` and return JPEG bytes.

    Thin shim over ``run_image_pipeline``: decode bytes, run the default
    pass list (EXIF -> deskew -> resize -> pad), encode back to JPEG. The
    PIL.Image IR stays in memory between passes; JPEG only at boundaries.

    When ``debug_dir`` is set (or the ``BEANBEAVER_PREOCR_DUMP_DIR`` env var
    points to a parent directory), each pass's output is snapshotted as
    JPEG alongside the original input and a ``trace.json`` recording every
    pass's metadata (deskew angle, resize ratio, etc).
    """
    resolved_dir = _resolve_dump_dir(image_bytes, debug_dir)
    if resolved_dir is not None:
        resolved_dir.mkdir(parents=True, exist_ok=True)
        (resolved_dir / "input.jpg").write_bytes(image_bytes)

    img = Image.open(io.BytesIO(image_bytes))
    ctx = ImagePipelineContext(debug_dir=resolved_dir)
    ops = default_image_pipeline(max_dimension=max_dimension, padding=padding)
    final, _ = run_image_pipeline(img, ops, ctx)

    buffer = io.BytesIO()
    final.convert("RGB").save(buffer, format="JPEG", quality=95)
    out_bytes = buffer.getvalue()

    if resolved_dir is not None:
        (resolved_dir / "output.jpg").write_bytes(out_bytes)
        (resolved_dir / "trace.json").write_text(json.dumps(ctx.trace, indent=2, default=str) + "\n")

    return out_bytes


_SUMMARY_LABEL_RE = re.compile(
    r"^\s*(?:SUB\s*T[OCQDG0]TAL|SUBTOTAL|TOTAL|HST|GST|PST|TAX|"
    r"MASTER(?:CARD)?|VISA|DEBIT|CREDIT|POINTS|CASH|CHANGE|BALANCE|"
    r"APPROVED|CARD|TERMINAL|MEMBER|AMOUNT|REFERENCE|AUTH)\b",
    re.IGNORECASE,
)


def _belongs_in_left_column(text: str, x_norm: float) -> bool:
    """
    Decide whether a detection sits in the LEFT (SKU/summary-label) column.

    Tokens with x_norm < 0.2 are unambiguously LEFT. In the 0.2-0.3
    transition band the answer depends on content: numeric SKU-style
    tokens (digits-led) and summary labels (TOTAL, TAX, …) belong on
    the LEFT; alpha-led short tokens like Costco's `CRAISINS 1.8` are
    descriptions and belong in MIDDLE.
    """
    if x_norm < 0.2:
        return True
    if x_norm >= 0.3:
        return False
    stripped = (text or "").lstrip()
    if not stripped:
        return False
    if stripped[0].isdigit():
        return True
    return _SUMMARY_LABEL_RE.match(stripped) is not None


def _boxes_overlap_y(det1: dict, det2: dict, min_overlap_ratio: float = 0.3) -> bool:
    """
    Check if two detection boxes overlap in Y-axis by at least min_overlap_ratio.

    This is more robust than center-distance comparison because it handles
    cases where items have tall bounding boxes that overlap vertically
    even when their centers are far apart.
    """
    y1_min, y1_max = det1["y_min"], det1["y_max"]
    y2_min, y2_max = det2["y_min"], det2["y_max"]

    overlap_start = max(y1_min, y2_min)
    overlap_end = min(y1_max, y2_max)

    if overlap_start >= overlap_end:
        return False

    overlap = overlap_end - overlap_start
    smaller_height = min(y1_max - y1_min, y2_max - y2_min)

    # Avoid division by zero for degenerate boxes
    if smaller_height <= 0:
        return False

    return overlap / smaller_height >= min_overlap_ratio


def _should_group_detections(det1: dict, det2: dict, image_width: int, y_threshold: int = 35) -> bool:
    """
    Determine if two detections should be grouped on the same line.

    Uses a hybrid approach:
    - For items on CLEARLY opposite sides (left vs right), use Y-overlap detection
    - For items on the SAME side or middle zone, use center-distance
    """
    # Normalize X positions to [0, 1] range
    x1_norm = det1["min_x"] / image_width
    x2_norm = det2["min_x"] / image_width

    # Determine if items are on CLEARLY opposite sides (one left, one right)
    det1_left = x1_norm < 0.3
    det1_right = x1_norm > 0.7
    det2_left = x2_norm < 0.3
    det2_right = x2_norm > 0.7

    opposite_sides = (det1_left and det2_right) or (det1_right and det2_left)

    if opposite_sides:
        # Use Y-overlap for opposite-side items (item + price pairing)
        return _boxes_overlap_y(det1, det2, min_overlap_ratio=0.5)
    # Use center-distance for same-side or middle items
    return abs(det1["center_y"] - det2["center_y"]) <= y_threshold


def _adaptive_middle_y_threshold(detections: list[dict]) -> float:
    """Compute adaptive Y-threshold for middle-column line merges."""
    heights = [det["y_max"] - det["y_min"] for det in detections if det["y_max"] > det["y_min"]]
    if not heights:
        return 24.0

    heights.sort()
    median_height = heights[len(heights) // 2]
    # Larger text/blur -> larger tolerance. Clamp to avoid cross-row merges.
    return max(12.0, min(30.0, median_height * 0.8))


def _line_y_span(line: list[dict]) -> tuple[float, float]:
    """Return (min_y, max_y) span for a grouped line."""
    return min(det["y_min"] for det in line), max(det["y_max"] for det in line)


def _line_center_y(line: list[dict]) -> float:
    """Return average center Y for a grouped line."""
    return sum(det["center_y"] for det in line) / len(line)


def _line_overlap_ratio(det: dict, line: list[dict]) -> float:
    """Return vertical overlap ratio between a detection and a line span."""
    line_min, line_max = _line_y_span(line)
    overlap_start = max(det["y_min"], line_min)
    overlap_end = min(det["y_max"], line_max)
    if overlap_start >= overlap_end:
        return 0.0

    overlap = overlap_end - overlap_start
    det_height = max(det["y_max"] - det["y_min"], 1e-6)
    line_height = max(line_max - line_min, 1e-6)
    return overlap / min(det_height, line_height)


def _distance_to_line_span(det: dict, line: list[dict]) -> float:
    """Return vertical distance from detection center to line span (0 if inside)."""
    center_y = det["center_y"]
    line_min, line_max = _line_y_span(line)
    if line_min <= center_y <= line_max:
        return 0.0
    if center_y < line_min:
        return line_min - center_y
    return center_y - line_max


def _group_detections_by_y_overlap(detections: list[dict], image_width: int = 1000) -> list[list[dict]]:
    """Group detections into lines using item-first matching."""
    if not detections:
        return []

    # Separate into LEFT, MIDDLE, RIGHT groups.
    #
    # SKUs cluster at x_norm <= ~0.15 and the description column starts
    # around 0.25-0.30. Tokens in the 0.2-0.3 band are ambiguous: real
    # SKUs (numeric) and summary labels (TOTAL/TAX/SUBTOTAL) belong on
    # the LEFT, while short numeric descriptions (e.g. Costco's
    # `CRAISINS 1.8`) belong in MIDDLE. Mis-routing a description into
    # LEFT makes it compete with the real SKU for the same price
    # column and cascades an off-by-one row drift through the items
    # block.
    left_items = []
    middle_items = []
    right_items = []

    for det in detections:
        x_norm = det["min_x"] / image_width
        if x_norm > 0.7:
            right_items.append(det)
        elif _belongs_in_left_column(det.get("text", ""), x_norm):
            left_items.append(det)
        else:
            middle_items.append(det)

    # Sort LEFT items by center_y (reading order: top to bottom)
    left_items.sort(key=lambda d: d["center_y"])
    # Sort RIGHT items by center_y for efficient matching
    right_items.sort(key=lambda d: d["center_y"])

    # Track which prices have been assigned
    assigned_prices = set()  # indices into right_items

    # Build lines by processing LEFT items in reading order
    lines: list[list[dict]] = []

    for left_det in left_items:
        # Find the FIRST unassigned price that overlaps with this item
        best_price = None
        best_price_idx = None

        for ri, right_det in enumerate(right_items):
            if ri in assigned_prices:
                continue
            if _boxes_overlap_y(left_det, right_det, min_overlap_ratio=0.3):
                best_price = right_det
                best_price_idx = ri
                break  # First match wins

        if best_price is not None:
            lines.append([left_det, best_price])
            assigned_prices.add(best_price_idx)
        else:
            # No overlapping price - item stands alone
            lines.append([left_det])

    # Add unassigned RIGHT items (orphan prices) as their own lines
    for ri, right_det in enumerate(right_items):
        if ri not in assigned_prices:
            lines.append([right_det])

    # Group MIDDLE items using adaptive center-distance plus span-overlap scoring
    y_threshold = _adaptive_middle_y_threshold(detections)
    overlap_threshold = 0.25
    for mid_det in middle_items:
        best_line_idx = None
        best_score = None

        for idx, line in enumerate(lines):
            overlap_ratio = _line_overlap_ratio(mid_det, line)
            center_distance = abs(mid_det["center_y"] - _line_center_y(line))

            if overlap_ratio < overlap_threshold and center_distance > y_threshold:
                continue

            # Prefer overlap-aligned line first, then shortest span/center distance.
            score = (
                0 if overlap_ratio >= overlap_threshold else 1,
                _distance_to_line_span(mid_det, line),
                center_distance,
            )
            if best_score is None or score < best_score:
                best_score = score
                best_line_idx = idx

        if best_line_idx is not None:
            lines[best_line_idx].append(mid_det)
        else:
            lines.append([mid_det])

    # Sort each line by X position (left to right)
    for line in lines:
        line.sort(key=lambda d: d["min_x"])

    # Sort lines by their average Y position (top to bottom)
    lines.sort(key=lambda line: sum(d["center_y"] for d in line) / len(line))

    return lines


def _clamp_unit_interval(value: float) -> float:
    """Clamp one float to the normalized [0, 1] bbox range."""
    return max(0.0, min(1.0, value))


def _normalized_bbox_from_points(points: list[list[float]], image_width: int, image_height: int) -> OcrBBox:
    """Convert a 4-point OCR polygon into a normalized axis-aligned bbox."""
    x_coords = [p[0] for p in points]
    y_coords = [p[1] for p in points]
    return {
        "left": _clamp_unit_interval(min(x_coords) / image_width),
        "top": _clamp_unit_interval(min(y_coords) / image_height),
        "right": _clamp_unit_interval(max(x_coords) / image_width),
        "bottom": _clamp_unit_interval(max(y_coords) / image_height),
    }


def transform_paddleocr_result(raw_result: dict[str, Any], padding: int = OCR_IMAGE_PADDING) -> OcrDocument:
    """
    Transform raw PaddleOCR result into the format expected by ocr_result_parser.

    Adjusts coordinates to account for padding added during image preprocessing.
    """
    # OCR returns dimensions of padded image; calculate original dimensions
    padded_width = raw_result["image_width"]
    padded_height = raw_result["image_height"]
    image_width = padded_width - 2 * padding
    image_height = padded_height - 2 * padding
    detections = raw_result.get("detections", [])

    if not detections:
        return {
            "schema_version": OCR_SCHEMA_VERSION,
            "engine": {"name": OCR_ENGINE_NAME_PADDLE, "version": None},
            "source": {
                "image_width": image_width,
                "image_height": image_height,
            },
            "status": "success",
            "full_text": "",
            "pages": [
                {
                    "page_index": 0,
                    "width": image_width,
                    "height": image_height,
                    "lines": [],
                }
            ],
        }

    # Parse raw PaddleOCR output into Detection dicts. Filtering, BOB-marker
    # removal, deskew, and reading-order sort are all post-OCR ops handled by
    # the detection pipeline below.
    detection_data: list[dict[str, Any]] = []
    for detection in detections:
        bbox, (text, confidence) = detection
        adjusted_bbox = [[p[0] - padding, p[1] - padding] for p in bbox]
        y_coords = [point[1] for point in adjusted_bbox]
        center_y = sum(y_coords) / len(y_coords)
        y_min = min(y_coords)
        y_max = max(y_coords)
        min_x = min(point[0] for point in adjusted_bbox)
        detection_data.append(
            {
                "bbox": adjusted_bbox,
                "text": text,
                "confidence": confidence,
                "center_y": center_y,
                "y_min": y_min,
                "y_max": y_max,
                "min_x": min_x,
            }
        )

    detection_data = normalize_detections(
        detection_data,
        image_width=image_width,
        image_height=image_height,
        operations=default_detection_pipeline(),
    )

    # Group into lines using hybrid Y-grouping
    lines = _group_detections_by_y_overlap(detection_data, image_width)

    # Convert to API format
    result_lines: list[dict[str, Any]] = []
    for line_idx, line in enumerate(lines, start=1):
        words: list[dict[str, Any]] = []
        line_confidence_sum = 0.0
        for word_idx, det in enumerate(line, start=1):
            normalized_bbox = _normalized_bbox_from_points(det["bbox"], image_width, image_height)
            confidence = float(det["confidence"])
            line_confidence_sum += confidence
            words.append(
                {
                    "id": f"word-{line_idx:04d}-{word_idx:04d}",
                    "text": det["text"],
                    "confidence": confidence,
                    "bbox": normalized_bbox,
                }
            )

        line_text = " ".join(str(w["text"]) for w in words)
        line_bbox = {
            "left": min(word["bbox"]["left"] for word in words),
            "top": min(word["bbox"]["top"] for word in words),
            "right": max(word["bbox"]["right"] for word in words),
            "bottom": max(word["bbox"]["bottom"] for word in words),
        }
        line_confidence = line_confidence_sum / len(words) if words else None
        result_lines.append(
            {
                "id": f"line-{line_idx:04d}",
                "text": line_text,
                "bbox": line_bbox,
                "confidence": line_confidence,
                "words": words,
            }
        )

    # Extract full text
    full_text = "\n".join(line["text"] for line in result_lines)

    return {
        "schema_version": OCR_SCHEMA_VERSION,
        "engine": {"name": OCR_ENGINE_NAME_PADDLE, "version": None},
        "source": {
            "image_width": image_width,
            "image_height": image_height,
        },
        "status": "success",
        "full_text": full_text,
        "pages": [
            {
                "page_index": 0,
                "width": image_width,
                "height": image_height,
                "lines": result_lines,
            }
        ],
    }
