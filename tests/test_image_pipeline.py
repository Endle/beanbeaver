"""Tests for the pre-OCR image pipeline."""

from __future__ import annotations

import io
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch
from beanbeaver.receipt.image_pipeline import (
    MAX_IMAGE_DIMENSION,
    OCR_IMAGE_PADDING,
    ImagePipelineContext,
    _detect_skew_angle,
    default_image_pipeline,
    deskew_affine_op,
    exif_transpose_op,
    make_pad_white_op,
    make_resize_max_dim_op,
    run_image_pipeline,
)
from beanbeaver.receipt.ocr_extraction import resize_image_bytes
from PIL import Image, ImageDraw


def _striped_text_image(width: int = 400, height: int = 600, rows: int = 12) -> Image.Image:
    """Synthetic receipt-like image: dark horizontal bars on white background."""
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    row_height = height // (rows * 2)
    margin = width // 8
    for i in range(rows):
        y = i * 2 * row_height + row_height
        draw.rectangle([(margin, y), (width - margin, y + row_height)], fill="black")
    return img


def test_exif_transpose_op_preserves_plain_image() -> None:
    img = _striped_text_image()
    ctx = ImagePipelineContext()
    out = exif_transpose_op(img, ctx)
    assert out.size == img.size
    assert ctx.trace == [{"pass": "exif_transpose", "size": img.size}]


def test_resize_op_skips_small_image() -> None:
    img = _striped_text_image(width=200, height=300)
    ctx = ImagePipelineContext()
    op = make_resize_max_dim_op(max_dimension=3000)
    out = op(img, ctx)
    assert out.size == (200, 300)
    assert ctx.trace[-1]["applied"] is False


def test_resize_op_caps_long_edge() -> None:
    img = Image.new("RGB", (6000, 3000), "white")
    ctx = ImagePipelineContext()
    op = make_resize_max_dim_op(max_dimension=3000)
    out = op(img, ctx)
    assert out.size == (3000, 1500)
    assert ctx.trace[-1]["applied"] is True


def test_pad_op_adds_border() -> None:
    img = _striped_text_image(width=100, height=100)
    ctx = ImagePipelineContext()
    op = make_pad_white_op(padding=20)
    out = op(img, ctx)
    assert out.size == (140, 140)
    assert ctx.trace[-1]["padding"] == 20


def test_pad_op_skipped_when_zero() -> None:
    img = _striped_text_image(width=100, height=100)
    ctx = ImagePipelineContext()
    op = make_pad_white_op(padding=0)
    out = op(img, ctx)
    assert out.size == (100, 100)
    assert ctx.trace[-1]["applied"] is False


def test_deskew_detects_known_angle() -> None:
    img = _striped_text_image()
    tilted = img.rotate(-4.0, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(255, 255, 255))
    angle, gain = _detect_skew_angle(tilted)
    assert abs(angle - 4.0) <= 1.0
    assert gain > 1.05


def test_deskew_noop_on_straight_image() -> None:
    img = _striped_text_image()
    ctx = ImagePipelineContext()
    out = deskew_affine_op(img, ctx)
    record = ctx.trace[-1]
    assert record["pass"] == "deskew_affine"
    assert record["applied"] is False
    assert out.size == img.size


def test_deskew_applies_rotation_when_tilted() -> None:
    img = _striped_text_image()
    tilted = img.rotate(-5.0, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(255, 255, 255))
    ctx = ImagePipelineContext()
    out = deskew_affine_op(tilted, ctx)
    record = ctx.trace[-1]
    assert record["applied"] is True
    assert abs(record["angle_deg"] - 5.0) <= 1.0
    assert out.size != tilted.size  # expand=True grows the canvas after rotation


def test_default_pipeline_trace_records_each_pass() -> None:
    img = _striped_text_image(width=200, height=300)
    ctx = ImagePipelineContext()
    ops = default_image_pipeline()
    out, returned_ctx = run_image_pipeline(img, ops, ctx)
    assert returned_ctx is ctx
    pass_names = [record["pass"] for record in ctx.trace]
    assert pass_names == ["exif_transpose", "resize_max_dim", "pad_white"]
    # Default padding wraps the image regardless of resize.
    assert out.size == (200 + 2 * OCR_IMAGE_PADDING, 300 + 2 * OCR_IMAGE_PADDING)


def test_resize_image_bytes_preserves_legacy_behavior_for_small_input() -> None:
    img = _striped_text_image(width=400, height=600)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=95)
    out_bytes = resize_image_bytes(buffer.getvalue())

    out_img = Image.open(io.BytesIO(out_bytes))
    expected_w = 400 + 2 * OCR_IMAGE_PADDING
    expected_h = 600 + 2 * OCR_IMAGE_PADDING
    assert out_img.size == (expected_w, expected_h)


def test_resize_image_bytes_respects_max_dimension() -> None:
    img = Image.new("RGB", (6000, 3000), "white")
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=95)
    out_bytes = resize_image_bytes(buffer.getvalue())

    out_img = Image.open(io.BytesIO(out_bytes))
    expected_w = MAX_IMAGE_DIMENSION + 2 * OCR_IMAGE_PADDING
    expected_h = MAX_IMAGE_DIMENSION // 2 + 2 * OCR_IMAGE_PADDING
    assert out_img.size == (expected_w, expected_h)


def test_env_var_triggers_per_pass_dump(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    import json

    from beanbeaver.receipt.ocr_helpers import PREOCR_DUMP_DIR_ENV

    img = _striped_text_image(width=400, height=600)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=95)
    image_bytes = buffer.getvalue()

    monkeypatch.setenv(PREOCR_DUMP_DIR_ENV, str(tmp_path))
    resize_image_bytes(image_bytes)

    subdirs = list(tmp_path.iterdir())
    assert len(subdirs) == 1, f"expected one per-receipt subdir, got: {subdirs}"
    dump_dir = subdirs[0]

    assert (dump_dir / "input.jpg").exists()
    assert (dump_dir / "output.jpg").exists()
    assert (dump_dir / "trace.json").exists()

    pass_files = sorted(p.name for p in dump_dir.glob("pass_*.jpg"))
    assert pass_files == [
        "pass_00_exif_transpose_op.jpg",
        "pass_01_resize_max_dim_op.jpg",
        "pass_02_pad_white_op.jpg",
    ]

    trace = json.loads((dump_dir / "trace.json").read_text())
    assert [r["pass"] for r in trace] == [
        "exif_transpose",
        "resize_max_dim",
        "pad_white",
    ]
