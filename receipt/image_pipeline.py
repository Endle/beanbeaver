"""Pre-OCR image pipeline.

LLVM-inspired sequence of image passes. The in-memory IR is ``PIL.Image.Image``;
JPEG bytes only cross the boundary on disk read, debug snapshots, and the OCR
HTTP POST. Each op is a pure ``(image, context) -> image`` function so passes
can be reordered, swapped, or unit-tested in isolation.

Default ordering: EXIF transpose -> deskew -> resize -> white pad. Deskew runs
before resize so the analysis sees full detail; padding runs last so rotated
corners get white fill rather than the pre-padded frame.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

MAX_IMAGE_DIMENSION = 3000
OCR_IMAGE_PADDING = 50

DESKEW_MAX_ANGLE_DEG = 10.0
DESKEW_ANGLE_STEP_DEG = 0.5
DESKEW_ANALYSIS_MAX_DIM = 1000
DESKEW_MIN_VARIANCE_GAIN = 1.05


@dataclass
class ImagePipelineContext:
    """Shared state threaded through image pipeline passes.

    ``trace`` is a mutable list each op may append a record to. ``debug_dir``,
    when set, receives a JPEG snapshot per pass for visual debugging.
    """

    merchant_hint: str | None = None
    debug_dir: Path | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)


ImagePipelineOp = Callable[[Image.Image, ImagePipelineContext], Image.Image]


def exif_transpose_op(img: Image.Image, ctx: ImagePipelineContext) -> Image.Image:
    """Apply EXIF orientation so downstream passes see a canonical frame."""
    out = ImageOps.exif_transpose(img)
    ctx.trace.append({"pass": "exif_transpose", "size": out.size})
    return out


def _detect_skew_angle(img: Image.Image) -> tuple[float, float]:
    """Return ``(angle_deg, variance_gain)`` from projection-profile analysis.

    Positive angle means the image is rotated clockwise; rotating by ``-angle``
    straightens it. ``variance_gain`` is the ratio of row-sum variance at the
    chosen angle vs. at 0 degrees; values near 1.0 mean no improvement.
    """
    gray = img.convert("L")
    width, height = gray.size
    longest = max(width, height)
    if longest > DESKEW_ANALYSIS_MAX_DIM:
        scale = DESKEW_ANALYSIS_MAX_DIM / longest
        analysis = gray.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.BILINEAR,
        )
    else:
        analysis = gray

    arr = np.asarray(analysis, dtype=np.uint8)
    threshold = int(arr.mean())
    binary = (arr < threshold).astype(np.uint8)

    angles = np.arange(
        -DESKEW_MAX_ANGLE_DEG,
        DESKEW_MAX_ANGLE_DEG + DESKEW_ANGLE_STEP_DEG / 2,
        DESKEW_ANGLE_STEP_DEG,
    )
    binary_img = Image.fromarray(binary * 255)

    best_angle = 0.0
    best_variance = -1.0
    baseline_variance = -1.0
    for angle in angles:
        rotated = binary_img.rotate(
            float(angle),
            resample=Image.Resampling.NEAREST,
            fillcolor=0,
        )
        row_sums = np.asarray(rotated, dtype=np.uint32).sum(axis=1)
        variance = float(row_sums.var())
        if abs(angle) < DESKEW_ANGLE_STEP_DEG / 2:
            baseline_variance = variance
        if variance > best_variance:
            best_variance = variance
            best_angle = float(angle)

    if baseline_variance <= 0:
        return 0.0, 1.0
    return best_angle, best_variance / baseline_variance


def deskew_affine_op(img: Image.Image, ctx: ImagePipelineContext) -> Image.Image:
    """Rotate the image so text rows are horizontal.

    Conservative: applies rotation only when the projection-profile variance
    gain over a zero-angle baseline exceeds ``DESKEW_MIN_VARIANCE_GAIN``. White
    fill keeps the OCR background consistent after rotation.
    """
    angle, gain = _detect_skew_angle(img)
    record: dict[str, Any] = {"pass": "deskew_affine", "angle_deg": angle, "variance_gain": gain}

    if abs(angle) < DESKEW_ANGLE_STEP_DEG or gain < DESKEW_MIN_VARIANCE_GAIN:
        record["applied"] = False
        ctx.trace.append(record)
        return img

    rotated = img.rotate(
        angle,
        resample=Image.Resampling.BICUBIC,
        expand=True,
        fillcolor=(255, 255, 255) if img.mode == "RGB" else 255,
    )
    record["applied"] = True
    record["size"] = rotated.size
    ctx.trace.append(record)
    return rotated


def make_resize_max_dim_op(max_dimension: int = MAX_IMAGE_DIMENSION) -> ImagePipelineOp:
    """Build a resize op that caps the longest side at ``max_dimension``."""

    def op(img: Image.Image, ctx: ImagePipelineContext) -> Image.Image:
        width, height = img.size
        if width <= max_dimension and height <= max_dimension:
            ctx.trace.append({"pass": "resize_max_dim", "applied": False, "size": img.size})
            return img
        if width > height:
            new_width = max_dimension
            new_height = int(height * (max_dimension / width))
        else:
            new_height = max_dimension
            new_width = int(width * (max_dimension / height))
        resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        ctx.trace.append({"pass": "resize_max_dim", "applied": True, "size": resized.size})
        return resized

    return op


def make_pad_white_op(padding: int = OCR_IMAGE_PADDING) -> ImagePipelineOp:
    """Build a pad op that surrounds the image with ``padding`` px of white."""

    def op(img: Image.Image, ctx: ImagePipelineContext) -> Image.Image:
        if padding <= 0:
            ctx.trace.append({"pass": "pad_white", "applied": False, "size": img.size})
            return img
        padded = ImageOps.expand(img, border=padding, fill="white")
        ctx.trace.append({"pass": "pad_white", "applied": True, "size": padded.size, "padding": padding})
        return padded

    return op


def default_image_pipeline(
    *,
    max_dimension: int = MAX_IMAGE_DIMENSION,
    padding: int = OCR_IMAGE_PADDING,
) -> list[ImagePipelineOp]:
    """Default pre-OCR ops in execution order."""
    return [
        exif_transpose_op,
        deskew_affine_op,
        make_resize_max_dim_op(max_dimension),
        make_pad_white_op(padding),
    ]


def run_image_pipeline(
    img: Image.Image,
    ops: Sequence[ImagePipelineOp],
    ctx: ImagePipelineContext | None = None,
) -> tuple[Image.Image, ImagePipelineContext]:
    """Apply ``ops`` in order, optionally snapshotting each pass output."""
    if ctx is None:
        ctx = ImagePipelineContext()
    current = img
    for index, op in enumerate(ops):
        current = op(current, ctx)
        if ctx.debug_dir is not None:
            ctx.debug_dir.mkdir(parents=True, exist_ok=True)
            snapshot_name = f"pass_{index:02d}_{op.__name__}.jpg"
            current.convert("RGB").save(ctx.debug_dir / snapshot_name, format="JPEG", quality=95)
    return current, ctx
