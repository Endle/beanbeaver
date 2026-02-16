"""Pure OCR transformation helpers for receipt parsing."""

import io
from typing import Any

MAX_IMAGE_DIMENSION = 3000  # Resize if either dimension exceeds this
OCR_IMAGE_PADDING = 50  # White padding around image to prevent edge truncation


def resize_image_bytes(
    image_bytes: bytes, max_dimension: int = MAX_IMAGE_DIMENSION, padding: int = OCR_IMAGE_PADDING
) -> bytes:
    """
    Resize image bytes if it exceeds max_dimension on either side.

    Also adds white padding around the image to prevent OCR edge truncation.

    Args:
        image_bytes: Image data as bytes
        max_dimension: Maximum allowed dimension (width or height)
        padding: White padding to add around image (pixels)

    Returns:
        Image bytes (JPEG format), resized if necessary, with padding added
    """
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(image_bytes))

    # Apply EXIF orientation to normalize the image
    # This ensures OCR and debug overlay see the same orientation
    img = ImageOps.exif_transpose(img)

    width, height = img.size

    # Check if resizing is needed
    if width <= max_dimension and height <= max_dimension:
        img_final = img
    else:
        # Calculate new dimensions while maintaining aspect ratio
        if width > height:
            new_width = max_dimension
            new_height = int(height * (max_dimension / width))
        else:
            new_height = max_dimension
            new_width = int(width * (max_dimension / height))

        img_final = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # Add white padding around the image to prevent OCR edge truncation
    if padding > 0:
        img_final = ImageOps.expand(img_final, border=padding, fill="white")

    # Convert to JPEG bytes
    buffer = io.BytesIO()
    img_final.convert("RGB").save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _boxes_overlap_y(det1: dict, det2: dict, min_overlap_ratio: float = 0.3) -> bool:
    """
    Check if two detection boxes overlap in Y-axis by at least min_overlap_ratio.

    This is more robust than center-distance comparison because it handles
    cases where items have tall bounding boxes that overlap vertically
    even when their centers are far apart.
    """
    y1_min, y1_max = det1["y_min"], det1["y_max"]
    y2_min, y2_max = det2["y_min"], det2["y_max"]

    overlap_start = max(y1_min, y2_min)
    overlap_end = min(y1_max, y2_max)

    if overlap_start >= overlap_end:
        return False

    overlap = overlap_end - overlap_start
    smaller_height = min(y1_max - y1_min, y2_max - y2_min)

    # Avoid division by zero for degenerate boxes
    if smaller_height <= 0:
        return False

    return overlap / smaller_height >= min_overlap_ratio


def _should_group_detections(det1: dict, det2: dict, image_width: int, y_threshold: int = 35) -> bool:
    """
    Determine if two detections should be grouped on the same line.

    Uses a hybrid approach:
    - For items on CLEARLY opposite sides (left vs right), use Y-overlap detection
    - For items on the SAME side or middle zone, use center-distance
    """
    # Normalize X positions to [0, 1] range
    x1_norm = det1["min_x"] / image_width
    x2_norm = det2["min_x"] / image_width

    # Determine if items are on CLEARLY opposite sides (one left, one right)
    det1_left = x1_norm < 0.3
    det1_right = x1_norm > 0.7
    det2_left = x2_norm < 0.3
    det2_right = x2_norm > 0.7

    opposite_sides = (det1_left and det2_right) or (det1_right and det2_left)

    if opposite_sides:
        # Use Y-overlap for opposite-side items (item + price pairing)
        return _boxes_overlap_y(det1, det2, min_overlap_ratio=0.5)
    # Use center-distance for same-side or middle items
    return abs(det1["center_y"] - det2["center_y"]) <= y_threshold


def _adaptive_middle_y_threshold(detections: list[dict]) -> float:
    """Compute adaptive Y-threshold for middle-column line merges."""
    heights = [det["y_max"] - det["y_min"] for det in detections if det["y_max"] > det["y_min"]]
    if not heights:
        return 24.0

    heights.sort()
    median_height = heights[len(heights) // 2]
    # Larger text/blur -> larger tolerance. Clamp to avoid cross-row merges.
    return max(12.0, min(30.0, median_height * 0.8))


def _line_y_span(line: list[dict]) -> tuple[float, float]:
    """Return (min_y, max_y) span for a grouped line."""
    return min(det["y_min"] for det in line), max(det["y_max"] for det in line)


def _line_center_y(line: list[dict]) -> float:
    """Return average center Y for a grouped line."""
    return sum(det["center_y"] for det in line) / len(line)


def _line_overlap_ratio(det: dict, line: list[dict]) -> float:
    """Return vertical overlap ratio between a detection and a line span."""
    line_min, line_max = _line_y_span(line)
    overlap_start = max(det["y_min"], line_min)
    overlap_end = min(det["y_max"], line_max)
    if overlap_start >= overlap_end:
        return 0.0

    overlap = overlap_end - overlap_start
    det_height = max(det["y_max"] - det["y_min"], 1e-6)
    line_height = max(line_max - line_min, 1e-6)
    return overlap / min(det_height, line_height)


def _distance_to_line_span(det: dict, line: list[dict]) -> float:
    """Return vertical distance from detection center to line span (0 if inside)."""
    center_y = det["center_y"]
    line_min, line_max = _line_y_span(line)
    if line_min <= center_y <= line_max:
        return 0.0
    if center_y < line_min:
        return line_min - center_y
    return center_y - line_max


def _group_detections_by_y_overlap(detections: list[dict], image_width: int = 1000) -> list[list[dict]]:
    """Group detections into lines using item-first matching."""
    if not detections:
        return []

    # Separate into LEFT, MIDDLE, RIGHT groups
    left_items = []
    middle_items = []
    right_items = []

    for det in detections:
        x_norm = det["min_x"] / image_width
        if x_norm < 0.3:
            left_items.append(det)
        elif x_norm > 0.7:
            right_items.append(det)
        else:
            middle_items.append(det)

    # Sort LEFT items by center_y (reading order: top to bottom)
    left_items.sort(key=lambda d: d["center_y"])
    # Sort RIGHT items by center_y for efficient matching
    right_items.sort(key=lambda d: d["center_y"])

    # Track which prices have been assigned
    assigned_prices = set()  # indices into right_items

    # Build lines by processing LEFT items in reading order
    lines: list[list[dict]] = []

    for left_det in left_items:
        # Find the FIRST unassigned price that overlaps with this item
        best_price = None
        best_price_idx = None

        for ri, right_det in enumerate(right_items):
            if ri in assigned_prices:
                continue
            if _boxes_overlap_y(left_det, right_det, min_overlap_ratio=0.3):
                best_price = right_det
                best_price_idx = ri
                break  # First match wins

        if best_price is not None:
            lines.append([left_det, best_price])
            assigned_prices.add(best_price_idx)
        else:
            # No overlapping price - item stands alone
            lines.append([left_det])

    # Add unassigned RIGHT items (orphan prices) as their own lines
    for ri, right_det in enumerate(right_items):
        if ri not in assigned_prices:
            lines.append([right_det])

    # Group MIDDLE items using adaptive center-distance plus span-overlap scoring
    y_threshold = _adaptive_middle_y_threshold(detections)
    overlap_threshold = 0.25
    for mid_det in middle_items:
        best_line_idx = None
        best_score = None

        for idx, line in enumerate(lines):
            overlap_ratio = _line_overlap_ratio(mid_det, line)
            center_distance = abs(mid_det["center_y"] - _line_center_y(line))

            if overlap_ratio < overlap_threshold and center_distance > y_threshold:
                continue

            # Prefer overlap-aligned line first, then shortest span/center distance.
            score = (
                0 if overlap_ratio >= overlap_threshold else 1,
                _distance_to_line_span(mid_det, line),
                center_distance,
            )
            if best_score is None or score < best_score:
                best_score = score
                best_line_idx = idx

        if best_line_idx is not None:
            lines[best_line_idx].append(mid_det)
        else:
            lines.append([mid_det])

    # Sort each line by X position (left to right)
    for line in lines:
        line.sort(key=lambda d: d["min_x"])

    # Sort lines by their average Y position (top to bottom)
    lines.sort(key=lambda line: sum(d["center_y"] for d in line) / len(line))

    return lines


def transform_paddleocr_result(raw_result: dict[str, Any], padding: int = OCR_IMAGE_PADDING) -> dict[str, Any]:
    """
    Transform raw PaddleOCR result into the format expected by ocr_result_parser.

    Adjusts coordinates to account for padding added during image preprocessing.
    """
    # OCR returns dimensions of padded image; calculate original dimensions
    padded_width = raw_result["image_width"]
    padded_height = raw_result["image_height"]
    image_width = padded_width - 2 * padding
    image_height = padded_height - 2 * padding
    detections = raw_result.get("detections", [])

    if not detections:
        return {
            "status": "success",
            "full_text": "",
            "pages": [{"lines": []}],
        }

    # Filter thresholds
    min_confidence = 0.7
    min_text_length = 2

    # Extract all detections with their positions, filtering low quality ones
    detection_data = []
    for detection in detections:
        bbox, (text, confidence) = detection

        # Filter out low confidence detections
        if confidence < min_confidence:
            continue

        # Filter out very short text (likely noise like single punctuation)
        if len(text.strip()) < min_text_length:
            continue

        # bbox is [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
        # Adjust coordinates to remove padding offset
        adjusted_bbox = [[p[0] - padding, p[1] - padding] for p in bbox]

        # Get y-coordinates for line grouping (both center and range)
        y_coords = [point[1] for point in adjusted_bbox]
        center_y = sum(y_coords) / len(y_coords)
        y_min = min(y_coords)
        y_max = max(y_coords)
        min_x = min(point[0] for point in adjusted_bbox)

        detection_data.append(
            {
                "bbox": adjusted_bbox,
                "text": text,
                "confidence": confidence,
                "center_y": center_y,
                "y_min": y_min,
                "y_max": y_max,
                "min_x": min_x,
            }
        )

    # Sort by y-coordinate first, then x-coordinate
    detection_data.sort(key=lambda d: (d["center_y"], d["min_x"]))

    # Group into lines using hybrid Y-grouping
    lines = _group_detections_by_y_overlap(detection_data, image_width)

    # Convert to API format
    result_lines: list[dict[str, Any]] = []
    for line in lines:
        words: list[dict[str, Any]] = []
        for det in line:
            # Normalize bbox to [0,1] range
            bbox_points = det["bbox"]
            x_coords = [p[0] for p in bbox_points]
            y_coords = [p[1] for p in bbox_points]

            def clamp(val: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
                return max(min_val, min(max_val, val))

            normalized_bbox = (
                (clamp(min(x_coords) / image_width), clamp(min(y_coords) / image_height)),
                (clamp(max(x_coords) / image_width), clamp(max(y_coords) / image_height)),
            )

            words.append(
                {
                    "text": det["text"],
                    "confidence": float(det["confidence"]),
                    "bbox": normalized_bbox,
                }
            )

        line_text = " ".join(str(w["text"]) for w in words)
        result_lines.append({"text": line_text, "words": words})

    # Extract full text
    full_text = "\n".join(line["text"] for line in result_lines)

    return {
        "status": "success",
        "full_text": full_text,
        "pages": [{"lines": result_lines}],
    }
