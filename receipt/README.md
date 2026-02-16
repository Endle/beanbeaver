# Receipt Component Boundary Note

`beanbeaver.receipt` is intended to become a mostly pure component focused on:
- parsing OCR boxes/text into structured receipt data
- formatting receipt data into Beancount text
- pure matching logic over already-loaded inputs

## Status

Runtime/side-effect modules have been migrated out of this package:
- `beanbeaver.runtime.receipt_storage`
- `beanbeaver.runtime.receipt_pipeline`
- `beanbeaver.runtime.receipt_server`

`beanbeaver.receipt` should remain focused on pure parsing/formatting/matching logic.
