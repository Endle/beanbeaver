#!/usr/bin/env python3
"""Manual e2e: PP-OCRv5 SERVER det + en mobile rec, NO doc-unwarp/doc-ori — i.e.
exactly what shipping server-det on-device would look like (our pipeline with the
det model swapped). Uses the module API (TextDetection/TextRecognition) to avoid
the full-pipeline bus-error on macOS. Emits .ocr.json for device_sim --cached.

Usage: manual_server_e2e.py <src_dir> <out_dir> [server|mobile]
"""
import glob
import json
import os
import shutil
import sys

import cv2
import numpy as np
from PIL import Image, ImageOps
from paddleocr import TextDetection, TextRecognition

MAX_DIM, PAD = 3000, 50


def resize_and_pad(path):
    img = ImageOps.exif_transpose(Image.open(path).convert("RGB"))
    w, h = img.size
    if max(w, h) > MAX_DIM:
        r = MAX_DIM / max(w, h)
        img = img.resize((max(1, round(w * r)), max(1, round(h * r))), Image.Resampling.LANCZOS)
    w, h = img.size
    c = Image.new("RGB", (w + 2 * PAD, h + 2 * PAD), (255, 255, 255))
    c.paste(img, (PAD, PAD))
    return np.array(c)  # RGB


def get_rotate_crop_image(img, points):
    points = np.array(points, dtype=np.float32)
    w = int(max(np.linalg.norm(points[0] - points[1]), np.linalg.norm(points[2] - points[3])))
    h = int(max(np.linalg.norm(points[0] - points[3]), np.linalg.norm(points[1] - points[2])))
    if w < 1 or h < 1:
        return None
    std = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    M = cv2.getPerspectiveTransform(points, std)
    dst = cv2.warpPerspective(img, M, (w, h), borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC)
    if dst.shape[0] * 1.0 / dst.shape[1] >= 1.5:
        dst = np.rot90(dst)
    return dst


def main():
    src, out = sys.argv[1], sys.argv[2]
    which = sys.argv[3] if len(sys.argv) > 3 else "server"
    os.makedirs(out, exist_ok=True)
    det = TextDetection(model_name=f"PP-OCRv5_{which}_det")
    rec = TextRecognition(model_name="en_PP-OCRv5_mobile_rec")

    for jpg in sorted(glob.glob(f"{src}/*.jpg")):
        stem = os.path.basename(jpg)[:-4]
        exp = f"{src}/{stem}.expected.json"
        if not os.path.exists(exp):
            continue
        rgb = resize_and_pad(jpg)
        H, W = rgb.shape[:2]
        polys = list(det.predict(rgb))[0]["dt_polys"]
        crops, boxes = [], []
        for p in polys:
            crop = get_rotate_crop_image(rgb, p)
            if crop is None:
                continue
            crops.append(crop)               # RGB crop; rec handles channel order
            boxes.append(np.asarray(p).tolist())
        dets = []
        if crops:
            for b, r in zip(boxes, rec.predict(crops)):
                dets.append([b, [r["rec_text"], float(r["rec_score"])]])
        json.dump({"status": "success", "image_width": W, "image_height": H, "detections": dets},
                  open(f"{out}/{stem}.ocr.json", "w"))
        shutil.copy(exp, f"{out}/{stem}.expected.json")
        shutil.copy(jpg, f"{out}/{stem}.jpg")
        print(f"{stem}: {len(dets)} dets", flush=True)


if __name__ == "__main__":
    main()
