"""Tests for detection normalization pipeline scaffolding."""

from beanbeaver.receipt.detection_normalization import normalize_detections


def test_normalize_detections_noop_passthrough() -> None:
    detections = [
        {"text": "A", "center_y": 1.0, "y_min": 0.5, "y_max": 1.5, "min_x": 10.0, "bbox": [[0, 0], [1, 1]]},
        {"text": "B", "center_y": 2.0, "y_min": 1.5, "y_max": 2.5, "min_x": 20.0, "bbox": [[2, 2], [3, 3]]},
    ]

    out = normalize_detections(detections, image_width=1000, image_height=2000)

    assert out == detections
    assert out is not detections
