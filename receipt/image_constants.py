"""Pre-OCR image constants — no heavy deps (no Pillow/numpy).

Single source of truth shared by the Rust-backed pipeline (``ocr_helpers``) and
the legacy Pillow ops (``image_pipeline``). Kept dependency-free so importing the
production pre-OCR path does not pull in Pillow.
"""

from __future__ import annotations

MAX_IMAGE_DIMENSION = 3000
OCR_IMAGE_PADDING = 50
JPEG_QUALITY = 95
