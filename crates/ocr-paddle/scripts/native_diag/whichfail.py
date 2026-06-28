import json, re, sys
from decimal import Decimal
from pathlib import Path
from beanbeaver.receipt.ocr_extraction import resize_image_bytes
from beanbeaver.receipt._rust import require_rust_matcher
from beanbeaver.receipt.ocr_result_parser import parse_receipt_from_raw
from beanbeaver.runtime import load_known_merchant_keywords, load_receipt_structuring_rule_layers

km = load_known_merchant_keywords(); rl = load_receipt_structuring_rule_layers()
stem = sys.argv[1]
base = Path("../beanbeaver-private-test/receipts_e2e")/stem
exp = json.load(open(str(base)+".expected.json"))
raw = require_rust_matcher().ocr_image_native(resize_image_bytes(base.with_suffix('.jpg').read_bytes()), "models-desktop")
cached = json.load(open(str(base)+".ocr.json"))

def parse(r):
    rec = parse_receipt_from_raw(r, item_category_rule_layers=rl, image_filename=stem, known_merchants=km)
    return [(it.description, str(it.price)) for it in rec.items]

def show(label, items):
    print(f"\n--- {label}: {len(items)} items ---")
    for d,p in items: print(f"   {p:>8}  {d}")

ours = parse(raw); cach = parse(cached)
print("EXPECTED:", len(exp["critical_items"]), "items")
exp_pp = {Decimal(e["price"]) for e in exp["critical_items"]}
ours_pp = {Decimal(p) for _,p in ours}; cach_pp={Decimal(p) for _,p in cach}
print("\nexpected prices MISSING from native:", sorted(exp_pp - ours_pp))
print("expected prices MISSING from cached:", sorted(exp_pp - cach_pp))
print("native prices NOT in expected (spurious):", sorted(ours_pp - exp_pp))
show("NATIVE", ours); show("CACHED", cach)
print("\nEXPECTED items:")
for e in exp["critical_items"]: print(f"   {e['price']:>8}  {e['description']}")
