"""Tests for OCR transformation helpers."""

from beanbeaver.receipt.ocr_helpers import transform_paddleocr_result


def _bbox(x0: int, y0: int, x1: int, y1: int) -> list[list[int]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_transform_filters_overlapping_bob_markers_keeps_real_item_lines() -> None:
    raw_result = {
        "status": "success",
        "image_width": 1000,
        "image_height": 1200,
        "detections": [
            [_bbox(20, 200, 820, 240), ["*xxxxxxxxxxBottom of Baske xxxxxxxxxxx", 0.95]],
            [_bbox(120, 210, 500, 250), ["232952 COKE ZERO", 0.99]],
            [_bbox(760, 210, 920, 248), ["17.19 H", 0.99]],
            [_bbox(40, 300, 500, 340), ["*x*********BOB Count 3", 0.95]],
            [_bbox(120, 320, 550, 360), ["305882 *KS IBU 400M", 0.99]],
            [_bbox(760, 324, 900, 356), ["16.99", 0.99]],
        ],
    }

    transformed = transform_paddleocr_result(raw_result, padding=0)
    full_text = transformed["full_text"]

    assert "Bottom of Baske" not in full_text
    assert "BOB Count 3" not in full_text
    assert "232952 COKE ZERO 17.19 H" in full_text
    assert "305882 *KS IBU 400M 16.99" in full_text
