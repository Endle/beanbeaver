"""Step 1 OCR Extraction Stage public API."""

from beanbeaver.receipt.detection_normalization import normalize_detections
from beanbeaver.receipt.ocr_helpers import (
    OCR_IMAGE_PADDING,
    MAX_IMAGE_DIMENSION,
    resize_image_bytes,
    transform_paddleocr_result,
)

__all__ = [
    "MAX_IMAGE_DIMENSION",
    "OCR_IMAGE_PADDING",
    "normalize_detections",
    "resize_image_bytes",
    "transform_paddleocr_result",
]
