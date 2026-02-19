# WARNING

New test cases shall be scrutinized that they don't contain PII (personally identifiable information).

## Add Receipt E2E Test (Brief)

1. Choose a case name (stem), for example `walmart_20260218_redact`.
2. Put required files in `vendor/beanbeaver/tests/receipts_e2e/`:
   - `<stem>.expected.json`
   - `<stem>.ocr.json`
   - `<stem>.jpg`
3. Create `<stem>.expected.json` with fields used by `test_e2e_receipts.py`:
   - `total` (required)
   - `date` (optional but recommended)
   - `merchant` (optional)
   - `critical_items` (optional list of key assertions)
4. Prefer adding `critical_items` for every clearly categorized item.
5. Keep fixtures redacted and free of PII.

Example expected file:

```json
{
  "merchant": "Walmart",
  "date": "2026-02-18",
  "total": "27.30",
  "critical_items": [
    {
      "description": "LYSOL BATH",
      "price": "3.97",
      "category": "Expenses:Home:HouseholdSupply"
    }
  ]
}
```

Run tests:

```bash
# Cached replay (.ocr.json -> parse)
pytest vendor/beanbeaver/tests/test_e2e_receipts.py -v --beanbeaver-e2e-mode cached -k "<stem-or-merchant>"

# Live OCR (.jpg -> OCR service -> parse)
pytest vendor/beanbeaver/tests/test_e2e_receipts.py -v --beanbeaver-e2e-mode live -k "<stem-or-merchant>"
```
