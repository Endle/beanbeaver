"""Stable OCR Extraction Stage schema definitions."""

from __future__ import annotations

from typing import TypedDict

OCR_SCHEMA_VERSION = "ocr.v1"
OCR_ENGINE_NAME_PADDLE = "paddleocr"


class OcrBBox(TypedDict):
    """Normalized axis-aligned bounding box."""

    left: float
    top: float
    right: float
    bottom: float


class OcrWord(TypedDict):
    """One OCR word/token."""

    id: str
    text: str
    bbox: OcrBBox
    confidence: float | None


class OcrLine(TypedDict):
    """One OCR line in reading order."""

    id: str
    text: str
    bbox: OcrBBox
    confidence: float | None
    words: list[OcrWord]


class OcrPage(TypedDict):
    """One OCR page."""

    page_index: int
    width: int
    height: int
    lines: list[OcrLine]


class OcrEngineInfo(TypedDict, total=False):
    """OCR engine metadata."""

    name: str
    version: str | None


class OcrSourceInfo(TypedDict, total=False):
    """Source image metadata."""

    image_width: int
    image_height: int
    image_sha256: str | None


class OcrDocument(TypedDict, total=False):
    """Stable OCR Extraction Stage document."""

    schema_version: str
    engine: OcrEngineInfo
    source: OcrSourceInfo
    pages: list[OcrPage]
    full_text: str
    status: str
    debug: dict
