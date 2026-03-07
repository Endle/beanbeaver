# Receipt JSON Pipeline Design

## Purpose

Replace the current parse-to-Beancount flow with a three-stage pipeline:

1. OCR Extraction Stage
2. Receipt Structuring Stage
3. Beancount Rendering Stage

The receipt JSON file is the source of truth while it exists. Beancount is a render target, not the persistence format for parsed receipt state.
The OCR artifact is an upstream intermediate input, not the review/persistence format.

## Goals

- Keep receipt parsing and review data machine-readable.
- Decouple OCR engine output from receipt structuring decisions.
- Preserve parser ambiguity and evidence instead of forcing early accounting decisions.
- Decouple parser classification from Beancount account mapping.
- Keep Beancount rendering simple and mostly stateless.
- Support multi-stage review/pass workflows by creating new JSON files instead of mutating old ones.

## Non-Goals

- Backward compatibility with old Beancount-as-source receipt files.
- A finalized v1 schema for the OCR artifact.
- Stable item IDs across full re-parse from OCR.
- Embedded history snapshots inside each stage file.
- Merge/split item review operations as first-class schema operations.

## Design Constraints

This design follows the repo receipt parsing policy in `CLAUDE.md`:

- Prefer missing items over wrong pairings.
- Spatial alignment is primary evidence when available.
- One price maps to at most one item.
- Ambiguous pairings remain unresolved and are surfaced via warnings/debug data.
- Printed receipt total is authoritative when available.

## Pipeline

### Step 1: OCR Extraction Stage

Input: Receipt image (`jpg`, `png`, ...)

Output: A list of bbox (bounding boxes)

Note: How to store these bbox is not decided yet.

This stage is responsible only for OCR extraction. It does not decide receipt semantics such as merchant, totals, or item structure.

### Step 2: Receipt Structuring Stage

Input: A list of bbox

Output: A structured receipt JSON document

Rules:

- The JSON should include receipt-level fields such as merchant, date, subtotal, tax, total, and currency when available.
- The JSON should include itemized records with rich metadata, warnings, and optional debug information.
- Human review data may be added.

The receipt structuring stage writes a JSON stage file containing:

- receipt-level detected fields
- optional human review overrides
- items with detected fields
- optional item-level review overrides
- warnings
- raw text
- optional debug payload
- lineage/stage metadata

This JSON document is the source of truth for parsed receipt state while it exists.

### Step 3: Beancount Rendering Stage

Input: Structured receipt JSON

Output: Beancount file

Rules:

- This stage should be lightweight and straightforward.
- User's custom item classification rules are introduced in this stage.

The renderer reads one JSON stage file and generates one Beancount file using simple rules:

- resolve effective values from detected fields plus `review.*` overrides
- render one posting per active item
- map semantic classification to Beancount accounts in Python code
- warn on missing classification and currently fall back to `Expenses:FIXME`
- fail if total is missing

## Storage Layout

### OCR artifact storage

Store OCR artifacts separately from receipt JSON and rendered Beancount output.

The exact on-disk format is intentionally unspecified for now. Current implementation details may remain as-is until bbox storage is settled.

Rules:

- Step 1 output should preserve the bbox data needed by Step 2.
- Raw OCR-engine payloads may continue to be stored for debugging/regressions.
- Receipt JSON may carry forward references to the OCR artifact or raw OCR payload.

### JSON stage storage

Use a dedicated JSON tree, separate from Beancount output.

Example:

```text
receipts/
  json/
    2026-03-03_costco_46_56_a1b/
      parsed.receipt.json
      review_stage_1.receipt.json
      review_stage_2.receipt.json
```

Rules:

- One receipt chain gets one directory.
- Directory name is human-readable and includes the shared 4-char UUID suffix.
- Stage filenames inside the directory are short and descriptive.
- All stage files in the same chain share the same `meta.receipt_id`.
- All stage files copy forward optional `debug.ocr_payload` verbatim for now.

### Beancount output storage

Rendered Beancount files live in a separate sibling tree, not inside the JSON tree.

Example:

```text
receipts/
  rendered/
    2026-03-03_costco_46_56_a1b.beancount
```

Rules:

- Output filename is stage-agnostic.
- Output filename uses effective reviewed values plus the shared UUID suffix.
- Rendering the latest stage replaces the prior output for that receipt chain.

## Stage Model

Each stage file is append-only at the file level:

- old stage files are not modified
- new passes or review actions create new stage files

Required stage metadata:

- `schema_version`
- `receipt_id`
- `stage`
- `stage_index`
- `created_at`
- `created_by`
- `pass_name`

Additional lineage metadata should be verbose and may include:

- `parent_file`
- `parent_receipt_id`
- `derived_from_file`
- `ocr_json_path`

`stage` remains a generic string, but the initial parser stage should default to `parsed`.

## Effective Value Rules

The stable schema stores detected values and optional review overrides separately.

Rules:

- detected values live under `receipt.*` and item fields
- receipt-level human overrides live under top-level `review.*`
- item-level human overrides live under `item.review.*`
- effective values are computed at read/render time
- effective values are not stored in JSON
- when review values exist, they always take precedence

Examples:

- effective merchant: `review.merchant` else `receipt.merchant`
- effective date: `review.date` else `receipt.date`
- effective item description: `item.review.description` else `item.description`
- effective item classification: merge detected classification with `item.review.classification`, with review winning on any provided field

## Top-Level Schema

The stable top-level shape is:

```json
{
  "meta": {},
  "receipt": {},
  "review": {},
  "items": [],
  "warnings": [],
  "raw_text": "optional string",
  "debug": {}
}
```

Rules:

- `review` is optional
- `warnings` may be empty
- `raw_text` is optional
- `debug` is optional

## `meta` Object

`meta` is the stable home for stage and lineage metadata.

Example:

```json
{
  "schema_version": "1",
  "receipt_id": "6d4f6f5f-8d6e-4a6e-8b56-4a3cc4972a34",
  "stage": "review_stage_1",
  "stage_index": 1,
  "created_at": "2026-03-07T18:42:00Z",
  "created_by": "human_review",
  "pass_name": "manual_edit",
  "parent_file": "parsed.receipt.json",
  "ocr_json_path": "../ocr_json/2026-03-03_costco_46_56.ocr.json"
}
```

Rules:

- `receipt_id` is a full UUID in JSON
- filenames use only the 4-char suffix derived from that UUID
- paths stored in metadata should be relative paths
- `created_by` is free-form text

Recommended `created_by` conventions:

- `receipt_parser`
- `human_review`
- username when available
- pass/tool name for automated stages

## `receipt` Object

Detected receipt-level values live only under `receipt.*`.

Example:

```json
{
  "merchant": "COSTCO",
  "date": "2026-03-03",
  "currency": "CAD",
  "subtotal": "41.20",
  "tax": "5.36",
  "total": "46.56"
}
```

Rules:

- all dates are ISO strings when present
- unknown date is `null`
- unknown merchant is `null`
- `subtotal`, `tax`, and `total` are strings when present
- `subtotal`, `tax`, and `total` may be `null`
- `total` is important but still optional at schema level
- confidence values do not belong in `receipt.*`

Removed from the new model:

- `date_is_placeholder`
- placeholder fake dates
- literal sentinel strings like `UNKNOWN_MERCHANT`

## Top-Level `review` Object

Receipt-level review overrides and notes live here.

Example:

```json
{
  "merchant": "COSTCO WHOLESALE",
  "date": "2026-03-03",
  "subtotal": "41.20",
  "tax": "5.36",
  "total": "46.56",
  "notes": "Verified manually"
}
```

Rules:

- `review` is optional
- all fields are optional
- when present, reviewed values override detected values
- `notes` is a single string for now

## `items` Array

There is one unified item array for parser-derived and human-added items.

Example item:

```json
{
  "id": "item-0001",
  "description": "Napa",
  "price": "3.17",
  "quantity": 1,
  "classification": {
    "category": "produce",
    "tags": ["grocery", "vegetable", "fresh"],
    "confidence": 0.82,
    "source": "rule_engine"
  },
  "warnings": [
    {
      "message": "maybe missed quantity detail",
      "source": "parser",
      "stage": "parsed"
    }
  ],
  "meta": {
    "source": "parser_spatial"
  },
  "review": {
    "description": "Napa cabbage",
    "classification": {
      "tags": ["grocery", "vegetable", "fresh", "cabbage"]
    },
    "notes": "confirmed manually"
  },
  "debug": {
    "source_line_text": "2.46 1b @ $1.29/1b 3.17",
    "source_line_index": 12,
    "description_source_text": "Napa",
    "price_source_text": "3.17",
    "quantity_source_text": "2.46 1b @ $1.29/1b",
    "line_bbox": [[0.06, 0.365], [0.92, 0.384]],
    "word_bboxes": [
      {
        "text": "Napa",
        "bbox": [[0.06, 0.365], [0.09, 0.372]],
        "confidence": 0.99
      },
      {
        "text": "3.17",
        "bbox": [[0.89, 0.377], [0.92, 0.384]],
        "confidence": 0.99
      }
    ]
  }
}
```

### Item field rules

- `description` is required
- `price` is required
- `quantity` is always explicitly present
- `price` means the authoritative line total for that item
- unit-price details belong only in debug/OCR-derived fields
- `id` is provisional and must not be depended on by other components yet
- IDs are stable only within the lifecycle of a specific JSON chain, not across re-parse from OCR
- `meta` is optional
- `review` is optional
- `debug` is optional
- `warnings` may be empty

### Item ordering

- preserve receipt order
- optional `order` may be present
- when `order` is absent, readers fall back to array order
- review-added items live in the same `items` array

### Human-added items

Human-added items use the same schema as parsed items.

Rules:

- OCR-derived debug fields may be absent
- minimal required fields are `description`, `price`, and `quantity`
- classification is optional
- item metadata should stay minimal
- recommended `item.meta.source` value: `human_review`

### Item removal

Do not physically delete removed items from later stage files.

Rules:

- carry items forward
- mark removals via `item.review.removed: true`
- renderer ignores removed items

Merge/split is not a first-class review operation. Review can achieve the same result through add/remove while keeping the design simpler.

## Classification Object

Classification is semantic, not Beancount-specific.

Shape:

```json
{
  "category": "produce",
  "tags": ["grocery", "vegetable", "fresh"],
  "confidence": 0.82,
  "source": "rule_engine"
}
```

Rules:

- parser classification and review classification share the same shape
- supported keys are exactly:
  - `category`
  - `tags`
  - `confidence`
  - `source`
- any field may be overridden in review
- Beancount accounts are not stored here

## Warnings

Use structured warnings, not plain strings.

Shape:

```json
{
  "message": "maybe missed item near price 8.99",
  "source": "parser",
  "stage": "parsed"
}
```

Rules:

- top-level `warnings` hold receipt-wide warnings
- `item.warnings` hold item-local warnings
- both shapes are the same
- no warning `code` field in v1
- no resolve/acknowledge state in v1
- single warning channel only; no severity hierarchy yet

## `raw_text`

`raw_text` is optional top-level data, outside `debug`.

Rules:

- it is the text used for Beancount OCR comments when present
- if missing, the renderer silently omits OCR comments

## `debug`

`debug` is optional, verbose, and non-contractual.

Rules:

- downstream components must not depend on `debug.*`
- the schema under `debug` may evolve freely
- `debug.ocr_payload` is allowed and should be copied forward between stages verbatim for now
- item-level OCR/bbox details live under `item.debug.*`

Examples of allowed top-level debug content:

- full OCR payload
- parser choice (`text` vs `spatial`)
- candidate lists
- heuristic traces
- timing details

## Renderer Rules

Step 3 should be a simple renderer over one JSON file.

### Input selection

- support explicit JSON path
- support default latest-stage selection
- latest stage is determined by shared `receipt_id` plus highest `stage_index`

### Effective receipt values

Use review override first, then detected value:

- merchant
- date
- subtotal
- tax
- total

### Effective item values

Use review override first, then detected value:

- description
- price
- quantity
- classification

### Classification mapping

Mapping from semantic classification to Beancount account lives in Python code, not in JSON.

Rules:

- keep mapping code isolated from parser internals
- mapping policy is `category` first, `tags` fallback
- if no usable mapping exists, warn and currently fall back to `Expenses:FIXME`
- this behavior may become a hard failure later

### Rendering behavior

- render one posting per active item
- do not merge items by account
- ignore items with `item.review.removed: true`
- include review-added items normally
- use receipt-level currency only
- render tax as a separate posting when present
- if review overrides total/subtotal/tax, trust them even when math conflicts, but warn
- if total is missing, refuse to render

### Beancount comments

Keep comments limited.

Include:

- receipt-level warnings near the transaction header
- item-level warnings near the corresponding posting
- full `raw_text` as comments when present

Do not include:

- original detected values when review overrides exist
- verbose lineage metadata comments
- debug payload comments

## Filename Rules

### Receipt chain directory

Use effective reviewed values plus the shared UUID suffix.

Example:

```text
2026-03-03_costco_46_56_a1b2/
```

### Stage files

Examples:

```text
parsed.receipt.json
review_stage_1.receipt.json
review_stage_2.receipt.json
```

### Rendered Beancount file

Example:

```text
2026-03-03_costco_46_56_a1b2.beancount
```

## Compatibility and Cleanup

- no backward compatibility is planned for old Beancount-as-source receipt files
- production may delete JSON after Beancount is committed
- that cleanup is manual for now

## Implementation Notes

Expected refactor direction:

1. Make the OCR extraction boundary explicit, while allowing the current artifact shape to remain implementation-defined for now.
2. Introduce receipt JSON schema types and serialization helpers.
3. Change receipt scan flow to save staged JSON instead of Beancount as the parsed artifact.
4. Add a renderer that reads JSON and writes Beancount.
5. Move storage/listing/review logic to load JSON stages, not Beancount.
6. Remove Beancount parsing as receipt-state reconstruction.
