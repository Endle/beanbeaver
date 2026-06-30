import json, re, glob, statistics as st
from decimal import Decimal
from pathlib import Path
from beanbeaver.receipt.ocr_extraction import resize_image_bytes
from beanbeaver.receipt._rust import require_rust_matcher
from beanbeaver.receipt.ocr_result_parser import parse_receipt_from_raw
from beanbeaver.runtime import load_known_merchant_keywords, load_receipt_structuring_rule_layers

km = load_known_merchant_keywords()
rl = load_receipt_structuring_rule_layers()
rust = require_rust_matcher()

def norm(t): return re.sub(r"[^A-Z0-9]", "", t.upper())
def toks(t): return set(w for w in re.sub(r"[^A-Z0-9 ]", " ", t.upper()).split() if w)
def cen(pts): return (sum(p[0] for p in pts)/len(pts), sum(p[1] for p in pts)/len(pts))

def linfit(X, Y):
    n=len(X); mx,my=st.mean(X),st.mean(Y)
    den=sum((x-mx)**2 for x in X) or 1.0
    a=sum((X[i]-mx)*(Y[i]-my) for i in range(n))/den
    return a, my-a*mx

def affine_to_cached(ours, cached):
    """Fit our=a*cached+b per axis from matched text; return fn mapping our-coord->cached frame."""
    def idx(d):
        m={}
        for det in d["detections"]:
            m.setdefault(norm(det[1][0]), []).append(cen(det[0]))
        return m
    oi, ci = idx(ours), idx(cached)
    ks=[k for k in oi if k and k in ci and len(oi[k])==1 and len(ci[k])==1]
    if len(ks) < 8: return None
    ox=[oi[k][0][0] for k in ks]; cx=[ci[k][0][0] for k in ks]
    oy=[oi[k][0][1] for k in ks]; cy=[ci[k][0][1] for k in ks]
    ax,bx=linfit(cx,ox); ay,by=linfit(cy,oy)  # our = a*cached + b
    if abs(ax)<1e-3 or abs(ay)<1e-3: return None
    return lambda x,y: ((x-bx)/ax, (y-by)/ay)  # our -> cached frame

def apply_affine(raw, fn):
    out={"image_width":raw["image_width"],"image_height":raw["image_height"],"detections":[]}
    for det in raw["detections"]:
        pts=[list(fn(p[0],p[1])) for p in det[0]]
        out["detections"].append([pts, det[1]])
    return out

def items_of(raw, fn_name):
    r=parse_receipt_from_raw(raw, item_category_rule_layers=rl, image_filename=fn_name, known_merchants=km)
    return [(it.description, it.price) for it in r.items]

def recall(parsed, expected):
    used=set(); hit=0
    for e in expected:
        ep=Decimal(str(e["price"])); ed=toks(e["description"])
        for i,(d,p) in enumerate(parsed):
            if i in used or p!=ep: continue
            pd=toks(d); ov=len(ed&pd)/max(1,len(ed))
            if ov>=0.5 or norm(e["description"])[:6] in norm(d) or norm(d)[:6] in norm(e["description"]):
                used.add(i); hit+=1; break
    return hit, len(expected)

files=sorted(glob.glob("../beanbeaver-private-test/receipts_e2e/*.jpg"))
tot={"ours":[0,0],"corr":[0,0],"cached":[0,0]}; nfit=0
for jpg in files:
    base=Path(jpg).with_suffix("")
    stem=base.name
    exp=json.load(open(str(base)+".expected.json")).get("critical_items",[])
    if not exp: continue
    resized=resize_image_bytes(Path(jpg).read_bytes())
    ours=rust.ocr_image_native(resized, "models-desktop")
    cached=json.load(open(str(base)+".ocr.json"))
    fn=affine_to_cached(ours, cached)
    for cond,raw in (("ours",ours),("cached",cached)):
        h,t=recall(items_of(raw,stem), exp); tot[cond][0]+=h; tot[cond][1]+=t
    if fn is not None:
        nfit+=1
        h,t=recall(items_of(apply_affine(ours,fn),stem), exp); tot["corr"][0]+=h; tot["corr"][1]+=t
    else:
        h,t=recall(items_of(ours,stem), exp); tot["corr"][0]+=h; tot["corr"][1]+=t

for k in ("ours","corr","cached"):
    h,t=tot[k]; print(f"{k:6}: {h}/{t}  = {100*h/t:.1f}%")
print(f"(affine fit applied to {nfit}/{len(files)} receipts)")
