# Receipt Component Boundary Note

`beanbeaver.receipt` is intended to become a mostly pure component focused on:
- Step 1 `beanbeaver.receipt.ocr_extraction`: OCR extraction helpers and normalization
- Step 2 `beanbeaver.receipt.receipt_structuring`: OCR-to-receipt parsing and staged receipt JSON
- Step 3 `beanbeaver.receipt.beancount_rendering`: rendering structured receipt data into Beancount
- pure matching logic over already-loaded inputs

## Status

Runtime/side-effect modules have been migrated out of this package:
- `beanbeaver.runtime.receipt_storage`
- `beanbeaver.runtime.receipt_pipeline`
- `beanbeaver.runtime.receipt_server`

`beanbeaver.receipt` should remain focused on pure OCR extraction, receipt structuring, Beancount rendering, and matching logic.
