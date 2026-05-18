# BeanBeaver

Beanbeaver turns bank statements and grocery receipts into your Beancount ledger.


** Two modes**
1. Import credit card and chequing statements into Beancount
2. Parse scanned grocery receipts into itemized expenses.

You can use either mode on its own, but using both brings the synergy of semi-automatic matching bank statements and grocery receipts.

## Example

[Input: T&T receipt](https://github.com/Endle/beanbeaver/blob/master/demo/receipt_groups/tnt_20251202/receipt_20260217_200222.jpg)

[Output: Itemized Beancount Record](https://github.com/Endle/beanbeaver/blob/master/demo/receipt_groups/tnt_20251202/2025-12-02_t_t_supermarket_32_70.beancount)


## CLI Usage

### Install

Recommended: Pixi

```bash
pixi install
pixi run maturin-develop
pixi run bb --help
```

Standard Python editable install:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev,test]"
maturin develop
python -m pip install -e ".[dev,test]"
bb --help
```

The Rust/PyO3 extension is required for receipt parsing and matching.


### Import Statement


```bash
bb import  # auto-detects type (prompts if ambiguous)
```
It scans your default Downloads folder and matches the bank.

### Parse receipt


#### 1. Launch PaddleOCR

We need to run [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) in container: <https://github.com/Endle/beanbeaver-ocr>
```
docker run --name beanbeaver-ocr -p 8001:8000 ghcr.io/endle/beanbeaver-ocr:latest
# Or podman on Linux
podman run --replace --name beanbeaver-ocr --network=slirp4netns -p 8001:8000 ghcr.io/endle/beanbeaver-ocr:latest
```

#### 2. Load receipt
If the receipt is on the mobile, we can run
```
bb serve
```

Then we use iOS shortcut or other tools to sent the receipt to this endpoint:
```
curl -X POST "http://<LAN_IP>:8080/beanbeaver" -F file=@receipt.jpg
```

The server always saves a draft to `receipts/scanned/` for later manual review.

On success the endpoint returns a JSON body that the iOS Shortcut can surface as a notification:

```json
{
  "status": "success",
  "summary": "Loblaws · 2026-05-16 · $32.70 · 8 items",
  "parsed": {
    "merchant": "Loblaws",
    "date": "2026-05-16",
    "date_is_placeholder": false,
    "total": "32.70",
    "subtotal": "29.10",
    "tax": "3.60",
    "item_count": 8,
    "warnings": []
  },
  "draft_filename": "review_stage_1.receipt.json"
}
```

On failure the body carries an `error_code` (`ocr_unreachable`, `ocr_error`, `parse_failed`, `internal_error`) and a human-readable `summary` you can show directly on the phone so you know whether to reshoot. Keep `bb serve` bound to localhost or your LAN — the response includes parsed merchant/date/amount.

#### 3. Edit receipt

```
bb edit
```
It will move `merchant.beancount` from `receipts/scanned` into `receipts/approved`

There are also helpers
```
bb list-approved
bb list-scanned
bb edit
bb re-edit
```

### Match Phase
Here comes the fun part.
```
bb match
```

It will match beancount records (from credit card statements) with receipts (in `/receipts/approved`)

**Notes:**
- `receipts/scanned/` means OCR+parser succeeded, but the draft is unreviewed and may contain errors.
- `receipts/approved/` means the draft has been reviewed and edited by a human.
- `bb edit` requires an interactive TTY.

## Development

Recommended local commands:

```bash
pixi run lint
pixi run test
pixi run test-e2e-cached
```

Core CI now targets Linux, macOS, and Windows for lint and non-E2E tests.
Container-backed OCR flows remain Linux-first in practice.
