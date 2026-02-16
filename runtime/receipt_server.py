"""FastAPI server for receiving receipt images from iPhone."""

import hashlib
import os
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from beanbeaver.receipt.formatter import format_parsed_receipt
from beanbeaver.receipt.ocr_helpers import resize_image_bytes, transform_paddleocr_result
from beanbeaver.receipt.ocr_result_parser import parse_receipt
from beanbeaver.runtime import get_logger, get_paths, load_known_merchant_keywords
from beanbeaver.runtime.receipt_pipeline import create_debug_overlay, save_ocr_json
from beanbeaver.runtime.receipt_storage import save_scanned_receipt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = get_logger(__name__)

_paths = get_paths()
RECEIPTS_DIR = _paths.receipts
SCANNED_DIR = _paths.receipts_scanned
OCR_JSON_DIR = _paths.receipts_ocr_json
OCR_SERVICE_URL = os.environ.get("OCR_SERVICE_URL", "http://localhost:8001")


class FixiOSMultipartMiddleware(BaseHTTPMiddleware):
    """Fix iOS Shortcuts multipart boundary issue (LF vs CRLF)."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        content_type = request.headers.get("content-type", "")
        logger.debug(f"Content-Type: {content_type}")

        if content_type.startswith("multipart/form-data"):
            body = await request.body()

            logger.debug(f"Original body length: {len(body)}")

            boundary_match = re.search(r"boundary=([^;]+)", content_type)
            if boundary_match:
                boundary = boundary_match.group(1).strip().strip('"')
                boundary_bytes = b"--" + boundary.encode()

                has_lf_only_boundary = re.search(rb"(?<!\r)\n" + re.escape(boundary_bytes), body) is not None
                if has_lf_only_boundary:
                    logger.info("Multipart boundary uses LF-only line endings; normalizing headers to CRLF.")
                else:
                    logger.info("Multipart boundary line endings look OK; normalizing headers anyway.")

                parts = body.split(boundary_bytes)
                fixed_parts: list[bytes] = []

                for i, part in enumerate(parts):
                    if i == 0:
                        fixed_parts.append(part)
                        continue

                    if part.startswith(b"--") or not part:
                        fixed_parts.append(part)
                        continue

                    leading = b""
                    if part.startswith(b"\r\n"):
                        leading = b"\r\n"
                        part_content = part[2:]
                    elif part.startswith(b"\n"):
                        leading = b"\n"
                        part_content = part[1:]
                    else:
                        part_content = part

                    if b"\r\n\r\n" in part_content:
                        header, body_rest = part_content.split(b"\r\n\r\n", 1)
                    elif b"\n\n" in part_content:
                        header, body_rest = part_content.split(b"\n\n", 1)
                    else:
                        fixed_parts.append(part)
                        continue

                    header = header.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                    fixed_parts.append(leading + header + b"\r\n\r\n" + body_rest)

                fixed_body = boundary_bytes.join(fixed_parts)
                logger.debug(f"Fixed body length: {len(fixed_body)}")

                async def receive() -> dict[str, Any]:
                    return {"type": "http.request", "body": fixed_body}

                request._receive = receive
            else:
                logger.info("Multipart request missing boundary; skipping normalization.")

        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create receipts directories on startup."""
    RECEIPTS_DIR.mkdir(exist_ok=True)
    SCANNED_DIR.mkdir(exist_ok=True)
    OCR_JSON_DIR.mkdir(exist_ok=True)
    yield


app = FastAPI(title="Receipt Scanner", lifespan=lifespan)
app.add_middleware(FixiOSMultipartMiddleware)


@app.post("/upload")
@app.post("/beanbeaver")
@app.post("/bb")
async def upload_receipt(request: Request) -> JSONResponse:
    """Receive a receipt image from iPhone and save parsed draft to scanned/."""
    form = await request.form()

    file = None
    for key, value in form.items():
        logger.debug(f"Form field: key={repr(key)}, type={type(value)}")
        if hasattr(value, "read"):
            file = value
            break

    if not file:
        return JSONResponse({"status": "error", "message": "No file found in request"}, status_code=400)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_filename = getattr(file, "filename", None)
    ext = Path(file_filename).suffix if file_filename else ".jpg"
    filename = f"receipt_{timestamp}{ext}"
    filepath = RECEIPTS_DIR / filename

    contents = await file.read()
    filepath.write_bytes(contents)

    try:
        image_sha256 = hashlib.sha256(contents).hexdigest()
        resized_contents = resize_image_bytes(contents)

        resized_filename = f"{filepath.stem}_resized.jpg"
        resized_filepath = RECEIPTS_DIR / resized_filename
        resized_filepath.write_bytes(resized_contents)
        logger.debug(f"Saved resized image to {resized_filepath}")

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{OCR_SERVICE_URL}/ocr",
                files={"file": (filename, resized_contents, "image/jpeg")},
            )
            if response.status_code == 200:
                raw_ocr_result = response.json()
                ocr_result = transform_paddleocr_result(raw_ocr_result)

                ocr_json_path = save_ocr_json(raw_ocr_result, filepath)
                logger.debug(f"Saved OCR JSON to {ocr_json_path}")

                try:
                    debug_image_path = create_debug_overlay(filepath, raw_ocr_result)
                    logger.debug(f"Created debug overlay: {debug_image_path}")
                except Exception as e:
                    logger.warning(f"Failed to create debug overlay: {e}")

                try:
                    receipt = parse_receipt(
                        ocr_result,
                        image_filename=filename,
                        known_merchants=load_known_merchant_keywords(),
                    )

                    # TODO(security): These stdout lines include merchant/date/amount/path details.
                    # Keep only for localhost-only operation; redact before non-localhost deployment.
                    print(f"\n{'=' * 60}")
                    print(f"Received: {filename}")
                    print(
                        f"Parsed: {receipt.merchant}, {receipt.date}, ${receipt.total:.2f}, {len(receipt.items)} items"
                    )
                    beancount_content = format_parsed_receipt(receipt, image_sha256=image_sha256)
                    output_path = save_scanned_receipt(receipt, beancount_content)
                    draft_filename = output_path.name
                    print(f"Saved for review: {output_path}")
                    print(f"{'=' * 60}\n")

                    return JSONResponse(
                        {
                            "status": "success",
                            "action": "saved_for_review",
                            "message": f"Saved for review: {draft_filename}",
                            "image_filename": filename,
                            "image_sha256": image_sha256,
                            "draft_filename": draft_filename,
                            "size_bytes": len(contents),
                        }
                    )

                except Exception as e:
                    logger.error(f"Failed to parse/format receipt: {e}")
            else:
                # TODO(security): This may include OCR payload text with PII.
                # Keep only for localhost-only operation; redact before non-localhost deployment.
                logger.error(f"OCR service error: {response.status_code} {response.text}")
    except httpx.RequestError as e:
        logger.error(f"OCR service unavailable: {e}")

    return JSONResponse(
        {"status": "error", "message": "Receipt processing failed"},
        status_code=500,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
