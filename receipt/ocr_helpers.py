"""Pure OCR transformation helpers for receipt parsing."""

import hashlib
import io
import json
import os
import time
from pathlib import Path
from typing import Any, cast

from PIL import Image

from ._rust import require_rust_matcher
from .detection_normalization import default_detection_pipeline, normalize_detections
from .image_pipeline import (
    MAX_IMAGE_DIMENSION,
    OCR_IMAGE_PADDING,
    ImagePipelineContext,
    default_image_pipeline,
    run_image_pipeline,
)
from .ocr_schema import OCR_ENGINE_NAME_PADDLE, OCR_SCHEMA_VERSION, OcrBBox, OcrDocument, OcrLine

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


def _group_detections_by_y_overlap(detections: list[dict], image_width: int = 1000) -> list[list[dict]]:
    """Group detections into lines using item-first matching.

    The clustering itself (left/middle/right routing, item↔price pairing, and
    middle-column attachment scoring) lives in the native extension; this shim
    maps the returned source indices back to the original detection dicts so all
    keys are preserved.
    """
    if not detections:
        return []
    index_groups = require_rust_matcher().group_detections_into_lines(detections, image_width)
    return [[detections[index] for index in group] for group in index_groups]


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
                "lines": cast("list[OcrLine]", result_lines),
            }
        ],
    }
