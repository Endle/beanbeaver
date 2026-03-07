#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

python - <<'PY'
import json
from pathlib import Path

from beanbeaver.receipt.ocr_helpers import transform_paddleocr_result
from beanbeaver.receipt.ocr_result_parser import parse_receipt
from beanbeaver.receipt.staged_json import build_parsed_receipt_stage, save_stage_document
from beanbeaver.runtime import load_item_category_rule_layers, load_known_merchant_keywords

fixture_dir = Path("tests/receipts_e2e")
ocr_path = fixture_dir / "costco_20260218_redact.ocr.json"
image_name = "costco_20260218_redact.jpg"
output_path = Path("demo/out/costco_20260218_redact.parsed.receipt.json")
output_path.parent.mkdir(parents=True, exist_ok=True)

raw_ocr_result = json.loads(ocr_path.read_text())
ocr_result = transform_paddleocr_result(raw_ocr_result)
rule_layers = load_item_category_rule_layers()

receipt = parse_receipt(
    ocr_result,
    image_filename=image_name,
    known_merchants=load_known_merchant_keywords(),
    item_category_rule_layers=rule_layers,
)
stage_document = build_parsed_receipt_stage(
    receipt,
    rule_layers=rule_layers,
    raw_ocr_payload=raw_ocr_result,
    ocr_json_path=str(ocr_path),
)
save_stage_document(output_path, stage_document)

print(output_path)
print(output_path.read_text())
PY
