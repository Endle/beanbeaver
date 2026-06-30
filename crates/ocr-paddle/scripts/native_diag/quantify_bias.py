import json, re, glob
from pathlib import Path
from beanbeaver.receipt.ocr_extraction import resize_image_bytes
from beanbeaver.receipt._rust import require_rust_matcher
rust = require_rust_matcher()

def norm(t): return re.sub(r"[^A-Z0-9]", "", t.upper())

def texts(raw):
    # full concatenated normalized OCR text (so split descriptions still match as substring)
    return "|".join(norm(d[1][0]) for d in raw["detections"])

only_c=only_n=both=neither=0
examples=[]
files=sorted(glob.glob("../beanbeaver-private-test/receipts_e2e/*.jpg"))
for jpg in files:
    base=Path(jpg).with_suffix("")
    exp=json.load(open(str(base)+".expected.json")).get("critical_items",[])
    if not exp: continue
    cached=json.load(open(str(base)+".ocr.json"))
    native=rust.ocr_image_native(resize_image_bytes(Path(jpg).read_bytes()), "models-desktop")
    ct, nt = texts(cached), texts(native)
    for e in exp:
        d=norm(e["description"])
        if len(d)<4: continue
        ic, ino = d in ct, d in nt
        if ic and not ino: only_c+=1; examples.append((base.name, e["description"], "cached-only"))
        elif ino and not ic: only_n+=1
        elif ic and ino: both+=1
        else: neither+=1
tot=only_c+only_n+both+neither
print(f"expected item descriptions (verbatim-normalized substring match in OCR):")
print(f"  in BOTH native & cached : {both}")
print(f"  in CACHED only (native differs)  : {only_c}   <-- ground-truth biased toward container")
print(f"  in NATIVE only (cached differs)  : {only_n}")
print(f"  in NEITHER (hard line, both off) : {neither}")
print(f"  total scored: {tot}")
print(f"\nif cached-only >> native-only, expected.json favors the container's exact OCR")
print("\nsample cached-only (expected desc native didn't reproduce verbatim):")
for r,d,_ in examples[:20]: print(f"   {r[:28]:28} {d}")
