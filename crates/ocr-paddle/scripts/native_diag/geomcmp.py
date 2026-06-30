import json, re, sys
from pathlib import Path
from beanbeaver.receipt.ocr_extraction import resize_image_bytes
from beanbeaver.receipt._rust import require_rust_matcher

stem = sys.argv[1] if len(sys.argv) > 1 else "foody_mart_20260219_214234"
base = Path("../beanbeaver-private-test/receipts_e2e") / stem
raw_img = base.with_suffix(".jpg").read_bytes()
resized = resize_image_bytes(raw_img)
ours = require_rust_matcher().ocr_image_native(resized, "models-desktop")
cached = json.loads(base.with_suffix(".ocr.json").read_text())

print(f"our frame:    {ours['image_width']} x {ours['image_height']}, {len(ours['detections'])} dets")
print(f"cached frame: {cached['image_width']} x {cached['image_height']}, {len(cached['detections'])} dets")

def norm(t): return re.sub(r"[^A-Z0-9]", "", t.upper())
def cy(pts): return sum(p[1] for p in pts) / len(pts)
def cx(pts): return sum(p[0] for p in pts) / len(pts)

def index(dets):
    d = {}
    for det in dets:
        pts, (text, conf) = det[0], det[1]
        d.setdefault(norm(text), []).append((cx(pts), cy(pts), text))
    return d

oi, ci = index(ours["detections"]), index(cached["detections"])
common = [k for k in oi if k and k in ci and len(oi[k]) == 1 and len(ci[k]) == 1]
rows = []
for k in common:
    ox, oy, t = oi[k][0]
    cx_, cy_, _ = ci[k][0]
    rows.append((cy_, oy - cy_, ox - cx_, t))
rows.sort()
print(f"\nmatched {len(rows)} unique-text lines. dy = ours - cached (padded px)")
print(f"{'cached_y':>8} {'dy':>6} {'dx':>6}  text")
for cyv, dy, dx, t in rows:
    print(f"{cyv:8.0f} {dy:6.1f} {dx:6.1f}  {t[:30]}")
import statistics as st
dys = [r[1] for r in rows]
print(f"\ndy mean={st.mean(dys):.1f} std={st.pstdev(dys):.1f} min={min(dys):.1f} max={max(dys):.1f}")
# warp signal: correlation of dy with vertical position
n = len(rows); ys = [r[0] for r in rows]
my, mdy = st.mean(ys), st.mean(dys)
cov = sum((ys[i]-my)*(dys[i]-mdy) for i in range(n))/n
var = st.pvariance(ys)
print(f"slope dy/y = {cov/var:.4f} px per px  (nonzero => progressive warp down the page)")
