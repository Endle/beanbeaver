# Native (container-free) OCR — progress & handoff

Status as of 2026-06-28. Branch: `mac` (commit `8898d02`, pushed to `origin/mac`).
This doc is the handoff for continuing the work on a Linux workstation.

## Goal

Let the desktop app do receipt OCR **in-process via ONNX**, without the PaddleOCR
podman/docker container — while keeping the container as an opt-in, higher-accuracy
backend. Secondary goal (done): unify the desktop parse path onto the shared MIT
`receipt-core` Rust pipeline (the same one iOS uses), so the only remaining
difference between backends is the OCR *engine*.

This is the desktop sibling of the iOS effort (`docs/ios_port.md`); the iOS app
ships mobile models, the desktop native path ships the **same models the container
uses** (server det + English mobile rec).

---

## What's done (this branch)

1. **Imported the `ocr-paddle` ONNX crate** (`crates/ocr-paddle/`) from the
   `ios_v2` branch into the workspace, behind a default Cargo feature
   `native-ocr` (so `ort`/ONNX Runtime + `image` only link when enabled).
   - `Cargo.toml`: added to `[workspace].members`, optional `ocr-paddle` + `image`
     deps, `[features] default = ["native-ocr"]`.

2. **PyO3 binding for native OCR** — `src/python_native_ocr.rs`:
   - `ocr_image_native(image_bytes: bytes, models_dir: str) -> dict` runs the
     ONNX det+cls+rec pipeline and returns the **container's `raw_result` shape**
     (`{image_width, image_height, detections: [[points, [text, conf]]]}`), so
     everything downstream is unchanged. Engine is cached in a `static Mutex`
     keyed by `models_dir`.

3. **PyO3 binding to push the parser boundary into Rust** —
   `receipt_parse_receipt_from_raw(raw_result, rule_layers, image_filename,
   known_merchants, current_year, padding=50)` in `src/python_receipt_parser.rs`:
   runs `receipt_core::ocr_transform::transform` + `receipt_parser::parse_receipt`
   with the **caller's runtime rules**, so no Python `transform_paddleocr_result`
   is needed on the live path.

4. **Python wiring**:
   - `runtime/receipt_pipeline.py`: `OCR_BACKEND` env switch (`container` default
     | `native`); `call_ocr_native()`; both `call_ocr_service`/`call_ocr_native`
     now return the raw dict only. Models dir from `BEANBEAVER_OCR_MODELS_DIR`,
     default repo `models-desktop/`.
   - `receipt/ocr_result_parser.py`: `parse_receipt_from_raw()` +
     `_native_to_receipt()` (shared dict→`domain.Receipt` mapping).
   - `application/receipts/scan.py` and `runtime/receipt_server.py` cut over to
     `parse_receipt_from_raw`. The write-only/unread `stage1.json` (transformed
     OCR) is dropped from the live path.
   - Python `transform_paddleocr_result` is **kept** as the parity-test oracle
     (not deleted).

5. **`.gitignore`**: model dirs (`models*/`), `*.onnx`, `.DS_Store` excluded —
   model weights are NOT committed (see "Models" below).

### Verification (on this Mac, Apple Silicon, CPU ONNX)
- **Equivalence**: `parse_receipt_from_raw` == old `transform_paddleocr_result →
  parse_receipt` path, **0/80 mismatches** on the private corpus.
- **Parity gate**: `tests/test_receipt_core_parity.py` 7/7.
- **Unit suite**: 304 passed (excl. e2e). `ruff` + `mypy` clean on changed files
  (pre-existing `crates/ocr-paddle/scripts/*.py` mypy errors remain — exclude or
  ignore).

---

## How it works (the seam)

```
image bytes
  → resize_image_bytes()            (receipt-image: EXIF, Lanczos cap 3000, pad 50, JPEG)
  → OCR backend:
       container:  HTTP POST {ocr_url}/ocr   → PaddleOCR (Linux) → raw_result
       native:     ocr_image_native(bytes, models_dir)  → ONNX (in-process) → raw_result
  → parse_receipt_from_raw(raw_result, rules, ...)   (receipt-core, Rust: transform+parse+categorize+format)
  → domain.Receipt
```

`OCR_BACKEND=native|container` (default `container`) selects the backend; the
`raw_result` contract and everything after it are identical.

---

## Build & run on Linux (primary handoff task)

Native ONNX is plain cross-platform Rust (`ort`, `image`, `imageproc`, `geo`);
ONNX Runtime is first-class on Linux. **The `coreml` feature is Apple-only and
OFF by default** — Linux runs pure CPU ONNX, exactly what was measured on Mac.
**Caveat: this has only been built/run on Apple-Silicon macOS. Linux is expected
to work but is UNVERIFIED — verifying it is step 1.**

```bash
# 1. Build the extension (native-ocr is a default feature; ort downloads the
#    Linux ONNX Runtime at build time).
pixi install
pixi run maturin-develop          # = maturin develop + editable pip install

# 2. Put the models in place (see "Models" below) → ./models-desktop/

# 3. Use native OCR
export OCR_BACKEND=native
# optional: export BEANBEAVER_OCR_MODELS_DIR=/abs/path/to/models-desktop
pixi run bb scan <image.jpg>      # or bb-tui / bb serve

# 4. (No container needed.) To compare against the container, run podman as
#    usual and set OCR_BACKEND=container.
```

If `ort` cannot download ONNX Runtime in your environment, configure it to use a
system `libonnxruntime` (see the `ort` crate's load-dynamic docs) — this is part
of the open "packaging" work.

---

## Models

The native path needs a `models-desktop/` dir with exactly one each of
`*_det.onnx`, `*_rec.onnx`, `*_ori.onnx` (resolved by suffix). The faithful
"same as the container" set mirrors `PaddleOCR(lang="en", ocr_version="PP-OCRv5")`
(see `../beanbeaver-ocr/ocr_service.py`):

| role | file | size | provenance |
|---|---|---|---|
| detection | `PP-OCRv5_server_det.onnx` | 88 MB | **NOT in git** — copy from this Mac, or re-export (below) |
| recognition (English) | `PP-OCRv5_mobile_rec.onnx` | 7.9 MB | tracked on `ios_v2`: `git show ios_v2:models/PP-OCRv5_mobile_rec.onnx > models-desktop/PP-OCRv5_mobile_rec.onnx` |
| textline orientation | `PP-LCNet_x1_0_textline_ori.onnx` | 6.8 MB | tracked on `ios_v2`: `git show ios_v2:models/PP-LCNet_x1_0_textline_ori.onnx > ...` |

> The recognizer hardcodes the **436-class English dict** (`crates/ocr-paddle/
> assets/en_ppocrv5_rec_dict.txt`), so it MUST be paired with the **English
> mobile rec**, NOT the 18,383-class server rec (that would decode garbage).

**Server det provenance**: it is untracked (gitignored, ~88 MB). Either:
- copy `models-desktop/` from this Mac (e.g. `scp`/`rsync`), or
- re-export with `paddle2onnx` from `~/.paddlex/official_models/PP-OCRv5_server_det`
  (see `docs/ios_port.md` for the export recipe).

A lighter alternative set `models-desktop-mobile/` (mobile det, 4.6 MB) exists —
faster but lower header accuracy (see findings).

---

## Findings (evidence; calibrated)

### A/B on the 80-receipt private corpus (`../beanbeaver-private-test`, `device_sim`)

| metric | container (cached server OCR) | native server-det | native mobile-det |
|---|---|---|---|
| merchant / date | 100% / 100% | 100% / 100% | 100% / 100% |
| total | 99% | 98% | 95% |
| **crit-items** | **97%** | **83%** | 82% |
| fully-correct | 89% | 51% | 46% |
| header (m/d/t all OK) | 99% | **98%** | 92% |
| latency / receipt (CPU) | n/a (network+paddle) | ~3.6 s (detect 1.9s) | ~1.7 s |
| det model size | — | 88 MB | 4.6 MB |

The cached `.ocr.json` baselines were generated by the actual container, so the
"container" column **is** the Linux-podman result (a container is host-independent).

**Server det is the right native default**: it holds header at 98% (the
CC-matching keys) vs mobile-det's 92%, despite being bigger/slower.

### Root-cause investigation of the item-recall gap (server det)

The gap is **item recall on dense receipts only** (small/clean receipts are at
100% native). Every single-root-cause hypothesis was tested and **rejected**:

- **Detection recall** — NOT it. `device_sim --attrib` on 110 item-failures:
  det-miss 9, bad-crop 36, true-rec 13, pairing 48, desk-miss 4. Only 9 are
  "line not found."
- **Global geometry / affine scale** — NOT it. Box diff vs container is a clean
  anisotropic affine (X≈0.88, Y≈0.96, same 789×3100 frame) with tiny non-affine
  residual (2–5px). Ablation (map our boxes into the container's coordinate scale,
  re-parse) did **not** recover recall: 89.8% → 88.5% (slightly worse).
- **Curl-warp / UVDoc dewarp** — NOT it. The small affine residual means it isn't
  curl; **do not build a dewarp model.**
- **Ground-truth container bias** — REAL but SMALL (~1% net). `expected.json` was
  seeded from the container's parse (`e2e_test.md` step 4) and carries its OCR
  typos (`Rasipberry`, `x:2`), but was human-corrected. Across the corpus,
  expected descriptions appear verbatim in cached-only 31 vs native-only 25 of
  635 → net ~1%. Not the explanation.

**Conclusion: no single lever.** The residual is a long tail — scattered
char-level rec slips (`O`↔`0`, `x`↔`Ã`), complex line structures (weight
`5.08 lb @ $7.99/lb`, multibuy `(2 /for $5.00)`), duplicate-price pairing. On
`bestco_20260213` native actually extracted **23 items vs the container's 18**.

**Scoring caveat**: strict scorer = 14pt gap (83 vs 97); fuzzy/price-based scorer
= ~7pt (90 vs 97). ~Half the measured gap is description-exactness a human
reviewer wouldn't care about. Header/matching is at parity either way.

### Platform note (corrected)

Native ONNX runs on **both** Linux and macOS; the container runs on both too.
The native-vs-container choice is the **same convenience↔accuracy tradeoff on
every OS — it is NOT platform-driven.** The only platform-specific facts:
- PaddleOCR's server-det **bus-errors running bare-metal on macOS-arm64 CPU**
  (irrelevant: the container runs Linux inside; native ONNX runs it fine on Mac).
- On Apple Silicon the container image likely runs under x86 emulation (slower),
  which makes native *more attractive on Mac* — but does not block Linux from
  native.

---

## Why ship both backends

- **Two complementary points on the convenience↔accuracy curve**: native =
  zero-dependency, offline, in-process, **matching-grade (98% header)**, ~83–90%
  items; container = the **itemization ceiling (97%)** that native provably can't
  reach (the edge is PaddleOCR-3.3.0-container-specific, not reproducible
  natively).
- **Fallback/resilience**: if the container is absent/down, native still scans.
- **Low marginal cost now**: post-unification they share one parser, transform,
  schema; only the engine + model distribution differ.

---

## Open work / next steps (ranked)

1. **Verify native on Linux** (build + run + a `device_sim` corpus run). Expect
   the same numbers as Mac (CPU ORT is deterministic across OS). This is the main
   reason for this handoff.
2. **Packaging** (platform-agnostic, currently the real blocker to turnkey
   native on ANY OS): bundle/locate the ONNX Runtime lib + ship the ~90 MB
   models (recommend download-on-first-run into a cache dir + a `bb fetch-models`
   helper).
3. **Measurement honesty**: switch the e2e item scorer to fuzzy/price matching
   (and/or regenerate a neutral ground truth not seeded from the container). This
   alone "closes" ~half the apparent gap and makes future native numbers honest.
4. **Housekeeping**: exclude `crates/ocr-paddle/scripts/*.py` from `mypy`
   (pre-existing errors); rename the bundled rec model to its `en_` name; decide
   whether `native-ocr` stays a default feature for Linux/Windows CI.
5. **Decision (product)**: ship native opt-in as-is (recommended). The engine
   investigation is effectively closed — no cheap single lever exists; the last
   itemization points require the full server stack (the container).

---

## Key files

- `crates/ocr-paddle/` — the ONNX OCR crate (det/rec/cls, DB postprocess).
- `crates/ocr-paddle/examples/device_sim.rs` — the measurement harness.
- `crates/ocr-paddle/scripts/scorecard.sh` — `--release` accuracy+latency wrapper.
- `src/python_native_ocr.rs` — `ocr_image_native` binding.
- `src/python_receipt_parser.rs` — `receipt_parse_receipt_from_raw` binding.
- `runtime/receipt_pipeline.py` — `OCR_BACKEND` switch, `call_ocr_native`.
- `receipt/ocr_result_parser.py` — `parse_receipt_from_raw`, `_native_to_receipt`.
- `rules/*.toml` — single source of truth for parser rules (embedded into
  `receipt-core` via `include_str!`, read at runtime by Python).

## Reproduce the measurements

```bash
# build the harness (always --release; debug latency is 10-50x inflated)
cargo build --release -p ocr-paddle --example device_sim

# container baseline (uses the committed .ocr.json), per-merchant:
./target/release/examples/device_sim ../beanbeaver-private-test/receipts_e2e --cached --by-merchant

# native (server det), per-merchant + per-stage failure attribution:
./target/release/examples/device_sim ../beanbeaver-private-test/receipts_e2e --models models-desktop --by-merchant
./target/release/examples/device_sim ../beanbeaver-private-test/receipts_e2e --models models-desktop --attrib

# native vs old-python parity (must be 0 mismatches):
#   see appendix script equiv.py
```

## Appendix — diagnostic scripts (committed under `crates/ocr-paddle/scripts/native_diag/`)

Run from the repo root via `pixi run python crates/ocr-paddle/scripts/native_diag/<script>`.
They need a built extension (`pixi run maturin-develop`), `./models-desktop/`, and
the `../beanbeaver-private-test` corpus.

- **`equiv.py`** — proves `parse_receipt_from_raw` == old `transform_paddleocr_result
  → parse_receipt` over the corpus (expect `0 mismatches`).
- **`ablation.py`** — maps our boxes into the container's coordinate scale
  (per-axis affine fit) and re-parses; reports item recall for ours / corrected /
  cached. Result: 89.8% → 88.5% → 97.4% → geometry/scale is NOT the cause.
- **`quantify_bias.py`** — how container-biased is `expected.json`: buckets each
  expected description by verbatim presence in native vs cached OCR
  (BOTH / CACHED-only / NATIVE-only / NEITHER). Result: 31 vs 25 of 635 (~1% net).
- **`affine_fit.py`** — fits `our = a*cached + b` per axis and reports the
  non-affine residual (small → not curl-warp).
- **`geomcmp.py`** — raw per-line dy/dx drift table (our vs cached boxes).
- **`whichfail.py`** — per-receipt native vs cached vs expected item diff (which
  items fail and why). Usage: `... whichfail.py <stem>`.

Note: these `native_diag/*.py` use the `beanbeaver` Python env (unlike the
PaddleOCR-venv scripts one level up). Add `crates/ocr-paddle/scripts/` to the
`mypy` exclude — they're throwaway diagnostics, not typed library code.
