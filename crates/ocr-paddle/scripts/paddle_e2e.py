#!/usr/bin/env python3
"""Run current PaddleOCR end-to-end on each fixture (our resize_and_pad image) and
emit .ocr.json in the desktop-service format, so device_sim --cached can score it
through the exact same parser. Isolates: current-paddle ceiling vs our live.

Usage: paddle_e2e.py <src_receipts_dir> <out_dir> [mobile|server]
"""
import json
import os
import shutil
import sys
import glob

import numpy as np
from PIL import Image, ImageOps
from paddleocr import PaddleOCR

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
    return np.array(c)


def main():
    src, out = sys.argv[1], sys.argv[2]
    which = sys.argv[3] if len(sys.argv) > 3 else "mobile"
    os.makedirs(out, exist_ok=True)
    kw = dict(use_textline_orientation=True, lang="en", device="cpu", ocr_version="PP-OCRv5")
    if which == "mobile":
        kw["text_detection_model_name"] = "PP-OCRv5_mobile_det"
    ocr = PaddleOCR(**kw)

    jpgs = sorted(glob.glob(f"{src}/*.jpg"))
    for jpg in jpgs:
        stem = os.path.basename(jpg)[:-4]
        exp = f"{src}/{stem}.expected.json"
        if not os.path.exists(exp):
            continue
        arr = resize_and_pad(jpg)
        h, w = arr.shape[:2]
        res = list(ocr.ocr(arr))
        dets = []
        for r in res:
            boxes = r.get("dt_polys") or r.get("rec_polys")
            texts = r.get("rec_texts")
            scores = r.get("rec_scores")
            if boxes is None or texts is None:
                continue
            for b, t, s in zip(boxes, texts, scores):
                bb = b.tolist() if hasattr(b, "tolist") else b
                dets.append([bb, [t, float(s)]])
        json.dump({"status": "success", "image_width": w, "image_height": h, "detections": dets},
                  open(f"{out}/{stem}.ocr.json", "w"))
        shutil.copy(exp, f"{out}/{stem}.expected.json")
        # also copy jpg so the dir is self-contained (device_sim filters on jpg+expected)
        shutil.copy(jpg, f"{out}/{stem}.jpg")
        print(f"{stem}: {len(dets)} dets", flush=True)


if __name__ == "__main__":
    main()
