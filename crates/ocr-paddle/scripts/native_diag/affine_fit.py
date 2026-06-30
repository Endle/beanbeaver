import json, re, sys, statistics as st
from pathlib import Path
from beanbeaver.receipt.ocr_extraction import resize_image_bytes
from beanbeaver.receipt._rust import require_rust_matcher

stem = sys.argv[1] if len(sys.argv) > 1 else "foody_mart_20260219_214234"
base = Path("../beanbeaver-private-test/receipts_e2e") / stem
resized = resize_image_bytes(base.with_suffix(".jpg").read_bytes())
ours = require_rust_matcher().ocr_image_native(resized, "models-desktop")
cached = json.loads(base.with_suffix(".ocr.json").read_text())
print(f"our frame:    {ours['image_width']} x {ours['image_height']}")
print(f"cached frame: {cached['image_width']} x {cached['image_height']}")

def norm(t): return re.sub(r"[^A-Z0-9]", "", t.upper())
def cen(pts): return (sum(p[0] for p in pts)/len(pts), sum(p[1] for p in pts)/len(pts))
def index(dets):
    d={}
    for det in dets:
        d.setdefault(norm(det[1][0]), []).append(cen(det[0]))
    return d
oi, ci = index(ours["detections"]), index(cached["detections"])
keys=[k for k in oi if k and k in ci and len(oi[k])==1 and len(ci[k])==1]
ox=[oi[k][0][0] for k in keys]; oy=[oi[k][0][1] for k in keys]
cx=[ci[k][0][0] for k in keys]; cy=[ci[k][0][1] for k in keys]
n=len(keys); print(f"matched {n} lines")

def linfit(X,Y):
    mx,my=st.mean(X),st.mean(Y)
    a=sum((X[i]-mx)*(Y[i]-my) for i in range(n))/sum((x-mx)**2 for x in X)
    b=my-a*mx
    res=[Y[i]-(a*X[i]+b) for i in range(n)]
    return a,b,res
# fit OURS = a*CACHED + b  (so a~1 means same scale)
ax,bx,rx=linfit(cx,ox); ay,by,ry=linfit(cy,oy)
print(f"X: our = {ax:.4f}*cached + {bx:.1f}   residual std = {st.pstdev(rx):.1f}px")
print(f"Y: our = {ay:.4f}*cached + {by:.1f}   residual std = {st.pstdev(ry):.1f}px")
print(f"raw dy std (no fit) = {st.pstdev([oy[i]-cy[i] for i in range(n)]):.1f}px")
# non-affine signal: does Y residual still correlate with X position? (keystone/curl)
mx=st.mean(cx); mry=st.mean(ry)
cov=sum((cx[i]-mx)*(ry[i]-mry) for i in range(n))/n
print(f"Y-residual vs X-position slope = {cov/st.pvariance(cx):.4f} (nonzero => keystone/curl, not pure scale)")
worst=sorted(range(n), key=lambda i: -abs(ry[i]))[:6]
print("largest Y residuals after affine fit:")
for i in worst:
    print(f"  cached=({cx[i]:.0f},{cy[i]:.0f}) Yresid={ry[i]:+.1f}  {keys[i][:24]}")
