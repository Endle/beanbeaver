"""Tests for detection normalization pipeline."""

from __future__ import annotations

import math

from beanbeaver.receipt.detection_normalization import (
    DESKEW_ANGLE_CAP_DEG,
    DESKEW_MIN_ANGLE_DEG,
    DetectionNormalizationContext,
    deskew_detections_op,
)
from beanbeaver.receipt.ocr_extraction import normalize_detections

IMAGE_WIDTH = 1000
IMAGE_HEIGHT = 4000


def _make_det(
    *,
    text: str,
    cx: float,
    cy: float,
    width: float,
    height: float = 30.0,
    confidence: float = 0.99,
) -> dict:
    x_min = cx - width / 2
    x_max = cx + width / 2
    y_min = cy - height / 2
    y_max = cy + height / 2
    bbox = [
        [x_min, y_min],
        [x_max, y_min],
        [x_max, y_max],
        [x_min, y_max],
    ]
    return {
        "bbox": bbox,
        "text": text,
        "confidence": confidence,
        "center_y": cy,
        "y_min": y_min,
        "y_max": y_max,
        "min_x": x_min,
    }


def _tilt(det: dict, angle_deg: float, image_width: int) -> dict:
    """Tilt a detection's y-coords as a real clockwise page rotation would.

    Same shear math as deskew_detections_op but in the inverse direction:
    feeding the tilted detections back into the op should recover ``angle_deg``.
    """
    tan_a = math.tan(math.radians(angle_deg))
    x_ref = image_width / 2
    bbox = det["bbox"]
    x_center = sum(p[0] for p in bbox) / len(bbox)
    delta = (x_center - x_ref) * tan_a
    new = dict(det)
    new["bbox"] = [[p[0], p[1] + delta] for p in bbox]
    new["center_y"] = det["center_y"] + delta
    new["y_min"] = det["y_min"] + delta
    new["y_max"] = det["y_max"] + delta
    return new


def _row_pair(text_item: str, price: str, cy: float) -> list[dict]:
    """A typical receipt row: an item on the left, a price on the right."""
    return [
        _make_det(text=text_item, cx=200, cy=cy, width=200),  # item: x in [100,300]
        _make_det(text=price, cx=850, cy=cy, width=80),       # price: x in [810,890]
    ]


def _straight_rows(n: int = 8) -> list[dict]:
    # Row spacing exceeds DESKEW_Y_WINDOW_PX (200) so the y-proximity filter
    # keeps only same-row item↔price candidates. Realistic receipts have
    # tighter spacing and rely on RANSAC to reject cross-row outliers; that
    # interaction is exercised by the live e2e tilt fixtures.
    rows: list[dict] = []
    for i in range(n):
        cy = 400 + i * 250
        rows.extend(_row_pair(f"ITEM {i}", f"{(i + 1) * 1.99:.2f}", cy))
    return rows


def test_normalize_detections_noop_passthrough() -> None:
    detections = [
        {"text": "A", "center_y": 1.0, "y_min": 0.5, "y_max": 1.5, "min_x": 10.0, "bbox": [[0, 0], [1, 1]]},
        {"text": "B", "center_y": 2.0, "y_min": 1.5, "y_max": 2.5, "min_x": 20.0, "bbox": [[2, 2], [3, 3]]},
    ]

    out = normalize_detections(detections, image_width=1000, image_height=2000)

    assert out == detections
    assert out is not detections


def test_deskew_no_candidates_when_empty() -> None:
    ctx = DetectionNormalizationContext(image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT)
    out = deskew_detections_op([], ctx)
    assert out == []
    record = ctx.trace[-1]
    assert record["applied"] is False
    assert record["gate_reason"] == "no_candidates"
    assert record["candidate_count"] == 0


def test_deskew_too_few_inliers() -> None:
    # Two rows -> at most a handful of cross-product candidates, well under 5.
    detections = _straight_rows(n=2)
    ctx = DetectionNormalizationContext(image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT)
    out = deskew_detections_op(detections, ctx)
    assert out == detections
    record = ctx.trace[-1]
    assert record["applied"] is False
    assert record["gate_reason"] == "too_few_inliers"


def test_deskew_no_op_on_straight_receipt() -> None:
    detections = _straight_rows(n=8)
    ctx = DetectionNormalizationContext(image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT)
    out = deskew_detections_op(detections, ctx)
    record = ctx.trace[-1]
    # Straight rows produce an angle near zero, which the small-angle gate
    # absorbs even when inliers/consensus are otherwise strong.
    assert record["applied"] is False
    assert record["gate_reason"] == "angle_too_small"
    assert abs(record["angle_deg"]) < DESKEW_MIN_ANGLE_DEG
    assert out == detections


def test_deskew_recovers_known_tilt() -> None:
    true_angle = 1.5  # degrees, clockwise
    straight = _straight_rows(n=8)
    tilted = [_tilt(det, true_angle, IMAGE_WIDTH) for det in straight]

    ctx = DetectionNormalizationContext(image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT)
    out = deskew_detections_op(tilted, ctx)
    record = ctx.trace[-1]

    assert record["applied"] is True
    assert record["gate_reason"] is None
    assert record["inlier_count"] >= 5
    assert record["consensus_ratio"] >= 0.60
    assert abs(record["angle_deg"] - true_angle) < 0.05

    # After shear, item and price center_y values within each original row
    # should re-align to within a fraction of a pixel.
    for i in range(8):
        item_y = out[2 * i]["center_y"]
        price_y = out[2 * i + 1]["center_y"]
        assert abs(item_y - price_y) < 1.0


def test_deskew_angle_too_large_is_rejected() -> None:
    huge_angle = DESKEW_ANGLE_CAP_DEG + 2.0
    straight = _straight_rows(n=8)
    tilted = [_tilt(det, huge_angle, IMAGE_WIDTH) for det in straight]

    ctx = DetectionNormalizationContext(image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT)
    out = deskew_detections_op(tilted, ctx)
    record = ctx.trace[-1]

    # Past the cap, every RANSAC sample is skipped; nothing is applied and
    # the detections pass through untouched.
    assert record["applied"] is False
    assert out == tilted


def test_deskew_ignores_low_confidence_detections() -> None:
    # Build tilted rows but mark every detection low-confidence: no candidate
    # passes the 0.95 floor, so the op must no-op with no_candidates.
    straight = _straight_rows(n=8)
    tilted = [_tilt(det, 1.5, IMAGE_WIDTH) for det in straight]
    for det in tilted:
        det["confidence"] = 0.80

    ctx = DetectionNormalizationContext(image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT)
    out = deskew_detections_op(tilted, ctx)
    record = ctx.trace[-1]
    assert record["gate_reason"] == "no_candidates"
    assert out == tilted


def test_deskew_requires_price_text_shape() -> None:
    # Replace every price with non-numeric text: prices stop qualifying so
    # no candidates form, even though items and layout are fine.
    straight = _straight_rows(n=8)
    tilted = [_tilt(det, 1.5, IMAGE_WIDTH) for det in straight]
    for i in range(1, len(tilted), 2):  # odd indices are price detections
        tilted[i]["text"] = "TAX"

    ctx = DetectionNormalizationContext(image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT)
    out = deskew_detections_op(tilted, ctx)
    record = ctx.trace[-1]
    assert record["gate_reason"] == "no_candidates"
    assert out == tilted
