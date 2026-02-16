# BeanBeaver

Beanbeaver turns bank statements and grocery receipts into your Beancount ledger.


** Two bodes**
1. Import credit card and chequing statements into Beancount
2. Parse scanned grocery receipts into itemized expenses.

You can use either mode on its own, but using both brings the synergy of semi-automatic matching bank statements and grocery receipts.

## Example

Input: [Loblaw receipt](https://github.com/Endle/beanbeaver/blob/master/tests/receipts_e2e/loblaw_20260211_censor.jpg)
Output:
```
2026-02-01 * "LOBLAW" "Receipt scan"
  Liabilities:CreditCard:PENDING   -16.41 CAD
  Expenses:Food:AlcoholicBeverage   13.99 CAD  ; COORS LIGHT 6 PK HQ
  Expenses:FIXME                     0.60 CAD  ; DEPOSIT 1 anuojal astgA.etteupit
  Expenses:FIXME                     1.82 CAD  ; H=HST 13% 13.99 @ 13.000%
```

## CLI Usage

### Install

In your root directory of the beancount directory

```
git submodule add https://github.com/Endle/beanbeaver.git vendor/beanbeaver
cp vendor/beanbeaver/flake.nix .
nix develop
```

For now it requires `nix`. If anyone needs it, I can remove the dependency to nix.


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


