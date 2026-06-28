import json, glob
from beanbeaver.receipt.ocr_helpers import transform_paddleocr_result
from beanbeaver.receipt.ocr_result_parser import parse_receipt, parse_receipt_from_raw
from beanbeaver.runtime import load_known_merchant_keywords, load_receipt_structuring_rule_layers

km = load_known_merchant_keywords()
rl = load_receipt_structuring_rule_layers()

def dump(r):
    return (r.merchant, str(r.date), str(r.total), r.date_is_placeholder,
            tuple((i.description, str(i.price), i.quantity, i.category) for i in r.items),
            str(r.tax), str(r.subtotal),
            tuple((str(t.amount), t.account, t.kind, t.raw_label) for t in r.tenders),
            tuple((w.message, w.after_item_index) for w in r.warnings))

files = sorted(glob.glob('../beanbeaver-private-test/receipts_e2e/*.ocr.json'))
mism = 0
for f in files:
    raw = json.loads(open(f).read())
    fn = f.split('/')[-1].replace('.ocr.json', '')
    old = parse_receipt(transform_paddleocr_result(raw), item_category_rule_layers=rl, image_filename=fn, known_merchants=km)
    new = parse_receipt_from_raw(raw, item_category_rule_layers=rl, image_filename=fn, known_merchants=km)
    if dump(old) != dump(new):
        mism += 1
        print('MISMATCH', fn)
        if dump(old)[:4] != dump(new)[:4]:
            print('  hdr old', dump(old)[:4], 'new', dump(new)[:4])
        if dump(old)[4] != dump(new)[4]:
            print('  items: old', len(old.items), 'new', len(new.items))
print(f'checked {len(files)} fixtures, {mism} mismatches')
