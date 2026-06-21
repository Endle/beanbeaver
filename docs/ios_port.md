# iOS Port — Plan & Progress

Status as of 2026-06-20. Branch: `ios`. This doc is the handoff for continuing
development on macOS.

## Goal

Move the **receipt parser** (capture → OCR → parse → categorize → beancount) to a
**fully on-device, serverless iOS app**. Bank-statement import, receipt↔transaction
matching, and ledger writes stay on desktop.

## Locked decisions

- **On-device PaddleOCR (PP-OCRv5)** — same model as the desktop `beanbeaver-ocr`
  service, so the parser needs no re-tuning (output is *close*, not byte-identical;
  see "Parity" below).
- **Native SwiftUI** app (iOS 17+, iPhone 15+). Not React Native — the hard parts
  (VisionKit, Rust FFI, ONNX Runtime) are all native; cross-platform isn't needed.
- **"Fat-Rust" seam**: Swift does capture + Core Image preprocess; **everything else
  runs in Rust** (ONNX inference, OCR post-processing, parse, categorize, format).
  Swift gets one call: image pixels → structured receipt + beancount text.
- **VisionKit document scanner** (`VNDocumentCameraViewController`) for capture —
  auto edge-detect + perspective deskew + crop + rotate (fixes the hold-steady /
  manual-crop pain; obsoletes the disabled homegrown deskew).
- **v1 endpoint**: phone displays the parsed receipt + beancount fragment and
  **exports** it (share-sheet / Files / iCloud). Desktop ingest + matching are later.
- **Bridge**: UniFFI → generated Swift, packaged as an `.xcframework` via SPM.

## Architecture

```
iPhone (offline, no server)
  VisionKit scan ──► Core Image preprocess (Swift)
        └─► Rust core (one UniFFI call):
              ocr-paddle: resize/pad → PP-OCRv5 det → cls → rec → CTC decode
              receipt-core: parse → categorize → beancount text
        └─► SwiftUI: show structured receipt + beancount; export file
────────────────────────────────────────────────────────────────────────
Desktop (unchanged, out of scope for v1): ingest exported fragment →
  bb-tui match → ledger
```

## Repo layout (Rust)

- `crates/receipt-core` (MIT, no Python/GPL) — pure receipt logic: OCR-result
  transform, parser, categorizer, beancount formatter, TOML rule loading. Exposes
  `process::process_receipt(detections, …) → ProcessedReceipt`.
- `crates/ocr-paddle` (MIT) — on-device OCR. Modules: `preprocess`, `db_postprocess`,
  `detect`, `recognize`, `classify`, `engine`, `process`. Exposes
  `process::process_image(engine, img, …) → ProcessedReceipt` and
  `engine::OcrEngine::from_paths(det, rec, cls)`.
- `crates/receipt-core/assets/…` and `crates/ocr-paddle/assets/ppocrv5_rec_dict.txt`
  (the 18,383-char recognition dictionary, committed).
- The legacy PyO3 extension (`src/`, lib `_rust_matcher`) now depends on
  `receipt-core`; desktop behavior unchanged.

## Progress

| Phase | Status | Commits |
|---|---|---|
| 0 — carve out `receipt-core` crate (no Python/GPL), cargo-deny license guard | ✅ shipped to master | PR #112 (`9883dc5`) |
| 1 — consolidate OCR glue into core; `process_receipt`; parity test vs legacy chain (byte-identical, 6 fixtures) | ✅ shipped to master | PR #112 |
| 2 (Rust) — `ocr-paddle`: PP-OCRv5 det + rec + CTC + cls + `process_image` | ✅ on `ios` | `3ff2072`, `89e0fad`, `dc5711e` |
| 3a — UniFFI binding (`crates/ffi`, host-verified) | ✅ on `ios` | — |
| 3b — cross-compile iOS targets + `.xcframework` | ✅ on `ios` | — |
| 2 (Swift) — SwiftUI app + local SPM package + `.xcodeproj` | ✅ builds + runs on iOS 26.5 sim | — |
| 4 — wire core into app + export | ✅ VisionKit scan + photo-picker → on-device scan → beancount + ShareLink; on-device scan verified via DEBUG bundled-sample path | — |
| 5 — validate/re-baseline vs all `tests/receipts_e2e/*` | ✅ on `ios` | — |

**Phase 5 (on-device validation)**: `crates/ocr-paddle/tests/phase5_e2e.rs` runs
`process_image` over every `tests/receipts_e2e/*.jpg` (11 with images) and checks
against the same `.expected.json` as the desktop Python e2e (merchant fuzzy /
date exact / total decimal / critical items), with OCR-tolerant item matching
(collapses letter-O↔digit-0, strips leading item codes, ignores spaces). Run:
`cargo test -p ocr-paddle --test phase5_e2e -- --ignored --nocapture`.
**Result: merchant/date/total + the large majority of items match the server on
all upright + mild-tilt fixtures.** 15 known parity gaps are tracked append-only
in `KNOWN_ON_DEVICE_GAPS` (public `expected.json` never weakened): (a) a few
single-char misreads even upright — TOMAX `PDR`→`PUR`, WING HING `SWT`→`SUT`;
(b) severe synthetic tilt (5°/7°) degrades detection (costco total reads `0.00`,
some prices shuffled). Mild tilt (3°) matches the server.

**xcframework (3b)**: `crates/ffi/build-xcframework.sh` builds release static libs
for `aarch64-apple-ios` + `aarch64-apple-ios-sim`, libtool-merges each with the
prebuilt `libonnxruntime.a`, and assembles `BBReceiptFFI.xcframework` (verified:
both slices carry `OrtGetApiBase` + the uniffi exports). Outputs into
`ios/BBReceiptKit/` (git-ignored). Sim slice is arm64-only (`INCLUDE_X86_SIM=1`
to add Intel).

**Swift app (2/4)**: `ios/` — local SPM package `BBReceiptKit` (binaryTarget +
`ReceiptScanner` conveniences) and `BeanBeaverScan` SwiftUI app (PhotosPicker →
`OcrSession.scan` off-main → render receipt + beancount + `ShareLink` export).
Hand-written `.xcodeproj` (no XcodeGen). `-scheme` build SUCCEEDS for the
simulator (iOS 26.5 runtime via `xcodebuild -downloadPlatform iOS`); app
installs + launches + renders on the iPhone 17 Pro sim (62 MB bundle incl.
models). NOTE: build with `-scheme` (not `-target` — the latter fails Swift
module resolution for the local package). Remaining: drive the PhotosPicker →
scan flow to visually confirm on-device OCR output (host FFI test already
proves the logic).

**UniFFI binding (3a)**: `crates/ffi` (`bb-receipt-ffi`, MIT) exposes a UniFFI
`OcrSession` object — `new(model_dir)` loads the 3 ONNX models once (Mutex-wrapped
`OcrEngine`), `scan(image_bytes, today, credit_card_account) → ReceiptResult`
(flattened `ProcessedReceipt`: merchant/date/total/tax/subtotal/items[]/warnings[]/
beancount). Host build clean (uniffi 0.28.3); FFI round-trip test
(`cargo test -p bb-receipt-ffi -- --ignored`) passes on the Costco fixture. Swift
bindings generate via `cargo run -p bb-receipt-ffi --bin uniffi-bindgen -- generate
--library target/debug/libbb_receipt_ffi.dylib --language swift --out-dir <dir>`.

**End-to-end validated**: `process_image` on `tests/receipts_e2e/costco_20260218_redact.jpg`
→ beancount with merchant COSTCO, date 2026-02-18, total 221.97, tax 4.44, 7
auto-categorized items. Detection alone: 43 boxes vs the server's 46 (~93%).
`cargo test -p ocr-paddle -- --include-ignored` → 10 passed.

## Model setup (REQUIRED — `models/` is git-ignored)

The ONNX models are **not committed** (large; bundle into the app instead). On the
Mac, fetch + convert them once. The desktop service uses **PaddleOCR 3.3.0,
PP-OCRv5, `lang='en'`, `use_textline_orientation=True`** — match it.

```bash
cd <repo>/models   # create if missing: mkdir -p models

# 1. Download official PP-OCRv5 mobile inference models (Paddle 3.0 PIR format)
for u in PP-OCRv5_mobile_det PP-OCRv5_mobile_rec PP-LCNet_x1_0_textline_ori; do
  wget "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/${u}_infer.tar"
done
for t in *.tar; do tar xf "$t"; done

# 2. Convert to ONNX. paddlepaddle needs Python 3.12 (no 3.13/3.14 wheels).
python3.12 -m venv /tmp/p2o && source /tmp/p2o/bin/activate
pip install --upgrade pip
pip install paddlepaddle paddle2onnx packaging   # 'packaging' is a missing transitive dep
for m in PP-OCRv5_mobile_det PP-OCRv5_mobile_rec PP-LCNet_x1_0_textline_ori; do
  paddle2onnx --model_dir "${m}_infer" --model_filename inference.json \
    --params_filename inference.pdiparams --save_file "${m}.onnx" --opset_version 14
done
deactivate
# Expect: PP-OCRv5_mobile_det.onnx (~4.8MB), _rec.onnx (~16.5MB), textline_ori.onnx (~6.7MB)
```

The recognition dictionary is already extracted and committed
(`crates/ocr-paddle/assets/ppocrv5_rec_dict.txt`) — no need to regenerate it.

## Building & testing

```bash
cargo test -p ocr-paddle                       # unit tests (no models needed)
cargo test -p ocr-paddle -- --include-ignored  # + end-to-end (needs models/ + fixtures)
cargo test -p receipt-core                      # pure-logic tests
pixi run test                                   # desktop Python suite (unchanged)
```

**macOS note:** `ort` links its own ONNX Runtime; no special flags expected.
**Linux note (this machine only):** `ort` needs `libstdc++` for the dev symlink the
system lacks — tests were run with `RUSTFLAGS="-L <repo>/.pixi/envs/default/lib"`.
Not needed on macOS.

## Remaining work (next steps on macOS)

1. **UniFFI binding** over `ocr-paddle::process::process_image`:
   - Define a UDL/proc-macro interface returning a record `{ merchant, date,
     total, tax, subtotal, items[], beancount }` (mirror `ProcessedReceipt`).
   - The Swift side passes decoded RGBA/RGB pixels + width/height (from a
     `CGImage`/`CIImage`) or encoded JPEG bytes; decode to `image::RgbImage`
     inside the binding.
   - Build a `cargo build --target aarch64-apple-ios` (+ `…-sim`) static lib and
     assemble an `.xcframework`; consume via Swift Package Manager. Bundle the
     three `.onnx` + the dict as app resources (or load from the xcframework).
2. **SwiftUI app**:
   - `VNDocumentCameraViewController` wrapped via `UIViewControllerRepresentable`.
   - Core Image: the Rust side already does resize-to-3000 + pad-50, so Swift can
     pass the scanned image fairly directly (or pre-resize for memory).
   - `@Observable` view models; a `ReceiptPipeline` service that calls the FFI.
   - Render structured receipt + beancount; **export** via `UIActivityViewController`
     / `.fileExporter`.
3. **Phase 5 validation**: run `process_image` over every `tests/receipts_e2e/*.jpg`
   (incl. `_tilt3/5/7`) and diff against `.ocr.json` / `.expected.json`;
   re-baseline private fixtures **append-only** (never overwrite — see
   `PRIVATE_TESTS.md` / `e2e_test.md`).

## On-device OCR quality (measured 2026-06-21, private corpus, 80 receipts)

`cargo run -p ocr-paddle --example device_sim -- <dir-or-image> [--cached] [--dump]`
runs the on-device pipeline on macOS (`live` = on-device ONNX models; `--cached`
= desktop PaddleOCR `.ocr.json` through the **same** `process_receipt`) and
scores vs `expected.json`. Same parser + images + scoring, so live-vs-cached
isolates OCR quality:

| metric | live (on-device) | cached (desktop PaddleOCR) |
|---|---|---|
| merchant | 92% | 100% |
| date | 81% | 100% |
| total | 82% | 99% |
| critical items | **51%** | **95%** |
| fully correct | **18%** | **84%** |

**Conclusion: the parser is excellent; the on-device OCR stage is the entire
bottleneck on real-world photos.** Failure modes: digit misreads on date/total
(`0263-01-23`, totals → `0.00`), dropped lines on dense receipts, occasional
text-orientation misses. Open question (determines the fix): model-variant gap
vs. our Rust det/rec port being weaker than reference PaddleOCR — the desktop
OCR is the external `ghcr.io/endle/beanbeaver-ocr` container (PaddleOCR), so
confirm its model variant there. Likely highest-leverage next step: try the
PP-OCRv5 **server** detection model on-device and re-measure with `device_sim`.

## Bigger-model experiment + decision (2026-06-21)

Question: does shipping bigger PP-OCRv5 models fix on-device quality? Measured on
the 80-receipt private corpus (`device_sim`, same parser/scoring, only OCR varies):

| metric | mobile (current) | hybrid (mob det+srv rec) | full-server | cached (desktop PaddleOCR) |
|---|---|---|---|---|
| merchant | 92% | 96% | 98% | 100% |
| date | 81% | 69% ↓ | 70% ↓ | 100% |
| total | 82% | 88% | 92% | 99% |
| crit-items | 51% | 60% | 68% | 95% |
| **fully correct** | **18%** | **26%** | **24%** | **84%** |

**Bigger weights are NOT the fix** — best heavy config reaches 26% vs the 84% the
same parser gets on desktop OCR, at +80–160 MB and ~3.7× latency (18.8 s/img on
Mac CPU), and they even regress date. The bottleneck is **our Rust OCR pipeline,
not the weights**. The desktop (`../beanbeaver-ocr`, paddleocr 3.3.0,
`PaddleOCR(use_textline_orientation=True, lang='en', ocr_version='PP-OCRv5')`,
**CPU**) runs PaddleOCR's full mature pipeline (its resize, DB postprocess,
doc/textline orientation, decode); our `ocr-paddle` reimplements only part of it.

**Decision: faithful Rust port** — keep on-device/serverless; close the gap by
matching PaddleOCR's detection pipeline behavior. Our DBPostProcess *params*
already match the model `inference.yml` (thresh 0.3, box_thresh 0.6, unclip 1.5,
resize_long 960), so the divergence is **algorithmic/subtle** (unclip is an
approximation, contour extraction, resize rounding, possible doc-orientation).
Localize with `device_sim --detcmp` (our boxes vs `.ocr.json` boxes). Bigger
models stay an option later (gated on a CoreML/ANE EP for latency).

**Faithful-port progress:**
- `--detcmp` localized the loss to **detection**: we cover only ~75% of
  PaddleOCR's lines (worse on dense receipts — `cnc` 46%, `fresh`/`foody` ~62%).
- **RESIZE_LONG 960 → 1536** (detect at higher res): recovers dense-receipt
  lines, lifting the private corpus **crit-items 51%→61%, fully-correct
  18%→24%, date 81%→88%** with the small mobile models — matching the
  bigger-model configs at *no* size cost. Net win, but a blunt one: it shifts
  a couple of public-fixture results (regresses upright costco `DOORDASH2X50`,
  tnt `WJ LIGHT…`; fixes WASABI tilts, WING HING) — Phase 5 gaps recalibrated.
- **Next:** the deeper lever is box-segmentation fidelity — even where line
  *counts* now match PaddleOCR, only ~62% of its boxes align with ours, so our
  contour→min-box→unclip construction still splits/merges/positions
  differently. Make it faithful to PaddleOCR (`get_mini_boxes`, the box
  expansion, multiple-of-128 resize rounding).

## Notes / gotchas

- **Parity is approximate**, by design: same model weights, but Core-Image vs PIL
  resize, ORT vs Paddle kernels, and our DB/CTC post-processing differ slightly, so
  on-device OCR is *close* to the server, not identical. The parser tolerates noise;
  re-baseline fixtures in Phase 5 as needed.
- **Licensing**: repo is GPL-2.0; the iOS-bound crates (`receipt-core`, `ocr-paddle`)
  are **MIT** so the App Store binary is distributable. `deny.toml` enforces
  permissive-only third-party Rust deps (CI: `.github/workflows/cargo-deny.yml`).
  Verify the ONNX stack licenses ship-clean (ONNX Runtime MIT; PP-OCRv5 Apache-2.0).
- **`imageproc::warp_into`** expects the **src→dst** projection (it inverts
  internally) — passing dst→src yields all-black crops.
- **`ort` 2.0.0-rc.12**: inputs are positional (`ort::inputs![tensor]`);
  `Session::inputs()` is a method (fields private); copy the output tensor to an
  owned `Vec` before borrowing `&self` again.
- **`ort` on iOS (proven 2026-06-21)**: `ort`'s default `download-binaries`
  feature auto-fetches a prebuilt `libonnxruntime.a` for both `aarch64-apple-ios`
  and `aarch64-apple-ios-sim` (cached under `~/Library/Caches/ort.pyke.io/`). No
  manual ORT cross-build needed. `crates/ffi` cross-compiles clean for both; the
  Rust staticlib is plain arm64 Mach-O. NOTE: the Rust `.a` does **not** embed
  `libonnxruntime.a` — the xcframework step must ship/link the ORT static lib too
  (combine via `libtool`, or add it as a separate xcframework slice).
- **Rec vocabulary**: `num_classes = blank(0) + dict(18383) + space` = 18385.
