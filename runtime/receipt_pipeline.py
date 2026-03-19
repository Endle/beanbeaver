"""Runtime helpers for receipt OCR pipeline (non-HTTP)."""

import json
import time
from pathlib import Path
from typing import Any

import httpx

from beanbeaver.receipt.ocr_extraction import OCR_IMAGE_PADDING, resize_image_bytes, transform_paddleocr_result
from beanbeaver.runtime import get_logger
from beanbeaver.runtime.receipt_storage import receipt_ocr_overlay_path, receipt_ocr_raw_path

logger = get_logger(__name__)


class OCRServiceUnavailable(RuntimeError):
    """Raised when the OCR service cannot be reached or returns an error."""


def call_ocr_service(receipt_path: Path, ocr_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Call the OCR service and return both raw and transformed results.

    Returns:
        Tuple of (raw_result, transformed_result).
    """
    ocr_url = ocr_url.rstrip("/")
    logger.info("Sending receipt to OCR service at %s...", ocr_url)

    try:
        image_bytes = receipt_path.read_bytes()
        resized_bytes = resize_image_bytes(image_bytes)

        start_time = time.time()
        response = httpx.post(
            f"{ocr_url}/ocr",
            files={"file": (receipt_path.name, resized_bytes, "image/jpeg")},
            timeout=60.0,
        )
        elapsed_time = time.time() - start_time
        logger.info("OCR service returned in %.2f seconds", elapsed_time)

        if response.status_code != 200:
            # TODO(security): This may include OCR payload text with PII.
            # Keep only for localhost-only operation; redact before non-localhost deployment.
            logger.error("OCR service error: %s - %s", response.status_code, response.text)
            raise OCRServiceUnavailable(f"OCR service error: {response.status_code}")

        raw_result = response.json()
        transformed_result = transform_paddleocr_result(raw_result)
        return raw_result, transformed_result

    except httpx.RequestError as e:
        logger.error("Failed to connect to OCR service: %s", e)
        raise OCRServiceUnavailable(f"Failed to connect to OCR service: {e}") from e


def save_ocr_json(ocr_result: dict[str, Any], receipt_path: Path, *, output_path: Path | None = None) -> Path:
    """Save OCR result JSON for debugging."""
    if output_path is None:
        ocr_json_path = receipt_path.with_suffix(".json")
    else:
        ocr_json_path = output_path
        ocr_json_path.parent.mkdir(parents=True, exist_ok=True)
    ocr_json_path.write_text(json.dumps(ocr_result, indent=2) + "\n", encoding="utf-8")
    logger.debug("OCR JSON saved to: %s", ocr_json_path)
    return ocr_json_path


def save_stage1_ocr_json(ocr_result: dict[str, Any], receipt_path: Path, *, output_path: Path | None = None) -> Path:
    """Save normalized Step 1 OCR output alongside the raw OCR payload."""
    if output_path is None:
        ocr_json_path = receipt_path.with_name(f"{receipt_path.stem}.stage1.json")
    else:
        ocr_json_path = output_path
        ocr_json_path.parent.mkdir(parents=True, exist_ok=True)
    ocr_json_path.write_text(json.dumps(ocr_result, indent=2) + "\n", encoding="utf-8")
    logger.debug("Stage 1 OCR JSON saved to: %s", ocr_json_path)
    return ocr_json_path


def create_debug_overlay(
    image_path: Path,
    raw_ocr_result: dict[str, Any],
    output_path: Path | None = None,
    padding: int = OCR_IMAGE_PADDING,
) -> Path:
    """
    Create a debug image with OCR bounding boxes overlaid on the OCR input image.

    This draws boxes on the same resized+padded image that was sent to OCR.
    """
    import io

    from PIL import Image, ImageDraw, ImageFont

    # Recreate the exact same resized+padded image that was sent to OCR
    image_bytes = image_path.read_bytes()
    resized_bytes = resize_image_bytes(image_bytes, padding=padding)
    img = Image.open(io.BytesIO(resized_bytes))
    img_width, img_height = img.size
    draw = ImageDraw.Draw(img)

    try:
        font_size = max(14, int(img_height / 150))
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    detections = raw_ocr_result.get("detections", [])

    for i, detection in enumerate(detections):
        bbox, (text, confidence) = detection
        points = [(p[0], p[1]) for p in bbox]
        points.append(points[0])  # close polygon

        if confidence > 0.9:
            color = (0, 255, 0)  # Green
        elif confidence > 0.7:
            color = (255, 255, 0)  # Yellow
        else:
            color = (255, 0, 0)  # Red

        line_width = max(2, int(img_width / 500))
        draw.line(points, fill=color, width=line_width)

        min_x = min(p[0] for p in points)
        min_y = min(p[1] for p in points)
        display_text = text[:30] + "..." if len(text) > 30 else text
        label = f"{i}: {display_text} ({confidence:.2f})"

        text_bbox = draw.textbbox((min_x, min_y - 18), label, font=font)
        draw.rectangle(text_bbox, fill=(255, 255, 255, 200))
        draw.text((min_x, min_y - 18), label, fill=(0, 0, 0), font=font)

    if output_path is None:
        output_path = image_path.parent / f"{image_path.stem}_debug.png"

    img.save(output_path)
    logger.info("Debug overlay saved to: %s", output_path)
    return output_path


def create_debug_overlay_from_json(image_path: Path, json_path: Path | None = None) -> Path:
    """Create debug overlay from an OCR JSON file."""
    if json_path is None:
        if image_path.parent.name == "source" and image_path.parent.parent.exists():
            receipt_dir = image_path.parent.parent
            json_path = receipt_ocr_raw_path(receipt_dir)
        else:
            json_path = image_path.with_suffix(".json")

    if not json_path.exists():
        raise FileNotFoundError(f"OCR JSON not found: {json_path}")

    raw_ocr_result = json.loads(json_path.read_text())
    output_path = None
    if image_path.parent.name == "source" and image_path.parent.parent.exists():
        output_path = receipt_ocr_overlay_path(image_path.parent.parent)
    return create_debug_overlay(image_path, raw_ocr_result, output_path=output_path)
