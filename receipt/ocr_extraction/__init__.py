"""Step 1 OCR Extraction Stage public API."""

from beanbeaver.receipt.detection_normalization import normalize_detections
from beanbeaver.receipt.image_pipeline import (
    ImagePipelineContext,
    ImagePipelineOp,
    default_image_pipeline,
    run_image_pipeline,
)
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
    "ImagePipelineContext",
    "ImagePipelineOp",
    "OcrBBox",
    "OcrDocument",
    "OcrEngineInfo",
    "OcrLine",
    "OcrPage",
    "OcrSourceInfo",
    "OcrWord",
    "default_image_pipeline",
    "normalize_detections",
    "resize_image_bytes",
    "run_image_pipeline",
    "transform_paddleocr_result",
]
