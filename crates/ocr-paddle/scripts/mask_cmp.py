#!/usr/bin/env python3
"""Compare our detection boxes vs PaddleOCR's fresh detection (same mobile model,
same 960 resolution, same padded image) to isolate mask (preprocess+inference)
fidelity from the already-verified-faithful postprocess. Also reports the dy
trend (shift vs scale) vs the cached .ocr.json baseline."""
import json
import sys

import numpy as np
from PIL import Image, ImageOps
from paddleocr import TextDetection

MAX_DIM, PAD = 3000, 50


def padded(path):
    img = ImageOps.exif_transpose(Image.open(path).convert("RGB"))
    w, h = img.size
    if max(w, h) > MAX_DIM:
        r = MAX_DIM / max(w, h)
        img = img.resize((max(1, round(w * r)), max(1, round(h * r))), Image.Resampling.LANCZOS)
    w, h = img.size
    c = Image.new("RGB", (w + 2 * PAD, h + 2 * PAD), (255, 255, 255))
    c.paste(img, (PAD, PAD))
    return np.array(c)


def aabb(q):
    q = np.asarray(q, np.float64).reshape(-1, 2)
    return np.array([q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()])


def iou(a, b):
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    ar = lambda x: max(0.0, x[2] - x[0]) * max(0.0, x[3] - x[1])
    u = ar(a) + ar(b) - inter
    return inter / u if u > 0 else 0.0


def match(A, B):
    aa, bb = [aabb(q) for q in A], [aabb(q) for q in B]
    cand = sorted(((iou(aa[i], bb[j]), i, j) for i in range(len(aa)) for j in range(len(bb)) if iou(aa[i], bb[j]) > 0.1), reverse=True)
    uo, up, m = set(), set(), []
    for v, i, j in cand:
        if i in uo or j in up:
            continue
        uo.add(i); up.add(j); m.append((aa[i], bb[j], v))
    return m, len(aa) - len(uo), len(bb) - len(up)


def report(label, A, B):
    m, ao, bo = match(A, B)
    if not m:
        print(f"  {label:<28} no matches (A={len(A)} B={len(B)})")
        return
    ious = np.array([x[2] for x in m])
    dcy = np.array([(a[1] + a[3]) / 2 - (b[1] + b[3]) / 2 for a, b, _ in m])  # A - B center y
    dh = np.array([(a[3] - a[1]) - (b[3] - b[1]) for a, b, _ in m])
    ycenters = np.array([(b[1] + b[3]) / 2 for a, b, _ in m])
    # linear fit dcy ~ slope*y + intercept: slope!=0 => vertical scale mismatch
    slope, intercept = np.polyfit(ycenters, dcy, 1) if len(m) > 2 else (0, dcy.mean())
    print(f"  {label:<28} A={len(A):<4} B={len(B):<4} matched={len(m):<4} A-only={ao:<3} B-only={bo:<3} "
          f"IoU(mean={ious.mean():.3f},>0.5={100*(ious>0.5).mean():.0f}%,>0.7={100*(ious>0.7).mean():.0f}%)")
    print(f"      dcy(A-B) mean={dcy.mean():+6.2f} median={np.median(dcy):+6.2f} std={dcy.std():5.2f} | "
          f"dh mean={dh.mean():+6.2f} | fit dcy={slope:+.4f}*y{intercept:+.1f}")


def main():
    dirs960 = sys.argv[1]   # probdump960 base
    dirs1536 = sys.argv[2]  # probdump base
    fixtures = sys.argv[3:]
    for fx in fixtures:
        stem = fx.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        cached = [d[0] for d in json.load(open(fx.replace(".jpg", ".ocr.json")))["detections"]]
        ours960 = json.load(open(f"{dirs960}/{stem}/ours_boxes.json"))
        ours1536 = json.load(open(f"{dirs1536}/{stem}/ours_boxes.json"))
        pad = padded(fx)
        det = TextDetection(model_name="PP-OCRv5_mobile_det")
        paddle960 = [p.tolist() for p in list(det.predict(pad))[0]["dt_polys"]]
        print(f"\n=== {stem} ===")
        report("ours@960  vs paddle@960", ours960, paddle960)   # mask fidelity (key)
        report("ours@960  vs cached", ours960, cached)
        report("paddle@960 vs cached", paddle960, cached)
        report("ours@1536 vs cached", ours1536, cached)
        report("ours@1536 vs paddle@960", ours1536, paddle960)


if __name__ == "__main__":
    main()
