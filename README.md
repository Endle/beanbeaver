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
pixi run bb --help
```

Standard Python editable install:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
bb --help
```

For contributors who want the Rust/PyO3 toolchain ready as well:

```bash
python -m pip install -e ".[dev,test]"
```


### Import Statement


```bash
bb import  # auto-detects type (prompts if ambiguous)
```
It would scan `~/Downloads` and match the bank

### Parse receipt


#### 1. Launch PaddleOCR

We need to run [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) in container: <https://github.com/Endle/beanbeaver-ocr>
```
docker run --name beanbeaver-ocr -p 8001:8000 ghcr.io/endle/beanbeaver-ocr:latest
# Or podman
podman run --replace --name beanbeaver-ocr --network=slirp4netns -p 8001:8000 ghcr.io/endle/beanbeaver-ocr:latest
```

#### 2. Load receipt
If the receipt is on computer, run

```bash
bb scan <image>        # opens editor, then stages to approved/
bb scan <image> --no-edit
```

If the receipt is on the mobile, we can run
```
bb serve
```

Then we use iOS shortcut or other tools to sent the receipt to this endpoint:
```
curl -X POST "http://<LAN_IP>:8080/beanbeaver" -F file=@receipt.jpg
```

The server always saves a draft to `receipts/scanned/` for later manual review.

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

