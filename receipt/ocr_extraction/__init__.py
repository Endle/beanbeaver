"""Step 1 OCR Extraction Stage public API."""

from beanbeaver.receipt.detection_normalization import normalize_detections
from beanbeaver.receipt.ocr_helpers import (
    MAX_IMAGE_DIMENSION,
    OCR_IMAGE_PADDING,
    resize_image_bytes,
    transform_paddleocr_result,
)
from beanbeaver.receipt.ocr_schema import (
    OCR_ENGINE_NAME_PADDLE,
    OCR_SCHEMA_VERSION,
    OcrBBox,
    OcrDocument,
    OcrEngineInfo,
    OcrLine,
    OcrPage,
    OcrSourceInfo,
    OcrWord,
)

__all__ = [
    "MAX_IMAGE_DIMENSION",
    "OCR_IMAGE_PADDING",
    "OCR_ENGINE_NAME_PADDLE",
    "OCR_SCHEMA_VERSION",
    "OcrBBox",
    "OcrDocument",
    "OcrEngineInfo",
    "OcrLine",
    "OcrPage",
    "OcrSourceInfo",
    "OcrWord",
    "normalize_detections",
    "resize_image_bytes",
    "transform_paddleocr_result",
]
