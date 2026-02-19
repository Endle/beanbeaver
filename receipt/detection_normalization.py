"""Detection normalization pipeline for OCR bbox detections.

This stage sits between raw detection extraction and line grouping.
The pipeline currently defaults to no-op behavior and is intended to
gradually absorb bbox-level normalization operations.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

Detection = dict[str, Any]


@dataclass(frozen=True)
class DetectionNormalizationContext:
    """Execution context shared by detection normalization operations."""

    image_width: int
    image_height: int
    merchant_hint: str | None = None


DetectionNormalizationOp = Callable[[list[Detection], DetectionNormalizationContext], list[Detection]]


def normalize_detections(
    detections: list[Detection],
    *,
    image_width: int,
    image_height: int,
    merchant_hint: str | None = None,
    operations: Sequence[DetectionNormalizationOp] | None = None,
) -> list[Detection]:
    """Run detection normalization operations in sequence.

    When `operations` is omitted, this is a no-op passthrough that returns
    the same detections in the same order.
    """
    context = DetectionNormalizationContext(
        image_width=image_width,
        image_height=image_height,
        merchant_hint=merchant_hint,
    )
    normalized = list(detections)
    for operation in operations or ():
        normalized = operation(normalized, context)
    return normalized
