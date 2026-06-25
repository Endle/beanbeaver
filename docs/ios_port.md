# iOS Port — Plan & Progress

Status as of 2026-06-25. Branch: `ios_v2`.
This doc is the handoff for continuing development on macOS.

## Goal

Move the **receipt parser** (capture → OCR → parse → categorize → beancount) to a
**fully on-device, serverless iOS app**. Bank-statement import, receipt↔transaction
matching, and ledger writes stay on desktop.

## Current status (2026-06-24)

**The prototype is built, runs on the iOS simulator and on a real iPhone, and
extracts receipts fully on-device.** What's done: UniFFI seam + `.xcframework`,
SwiftUI app (VisionKit capture + photo picker + export + "Save scans to Photos"),
Phase 5 validation, the `device_sim` macOS harness.

**The live question is on-device OCR quality.** On a 80-receipt real-world corpus
(`../beanbeaver-private-test`), the on-device pipeline scores **82% critical-items
/ 46% fully-correct / 95% total**, vs **97% / 89% / 99%** for the *same parser*
fed desktop PaddleOCR detections — and that cached figure **equals the desktop's
honest score exactly** (71/71 of the non-`known_failures` fixtures pass; see
"Scorer fix + corrected baseline" below). The **parser is fully faithful**; the
residual is our Rust OCR pipeline. Three findings closed most of the original gap:
- **Recognition model was wrong** (the big one, 2026-06-24): we shipped the
  18,383-class multilingual `PP-OCRv5_mobile_rec`, but the desktop baseline uses
  `en_PP-OCRv5_mobile_rec` (436-class **English**). The huge vocabulary mis-read
  digits/punct on clean crops. Switching to the English model lifted live
  **24%→44% fully, 61%→82% items, date 88%→100%** (see "Recognition model
  mismatch" below). Bigger *multilingual* weights never helped because the axis
  was wrong — narrower/English, not bigger.
- **Total reconciliation** (2026-06-24): box-position artifacts mis-paired the
  TOTAL label with the tax row (or nothing → `0.00`), but the correct total is
  still printed in the payment block. `extract_total` now reconciles against the
  card-tender / `AMOUNT:` echo, recovering the Costco totals: **live total
  90%→95%, fully 44%→46%**, cached unchanged (parity-safe). The 4 remaining
  total-fails read `0.00` (fail-loud; flagged for confirmation in matching).
- **Detection box positions** were *hypothesised* (2026-06-24) to be the dominant
  residual (`imageproc` Suzuki contours vs OpenCV). **DISPROVEN 2026-06-25** — our
  contour path and mask are both faithful to current PaddleOCR-*mobile*. The real
  root cause is the **detection model: cached uses PP-OCRv5 server det, we ship
  mobile det.** See "Box-position hypothesis closed + ceiling finding" below.

**Target (re-framed 2026-06-25, corrected):** measure the **shipped mobile client**
against the verified `expected.json` with a realistic "good enough" bar (date +
total correct, items ≥ 80%) — not all-or-nothing. By that bar the mobile client is
**62% good-enough / 92% header-correct** (see "Good-enough metric" below); the
"46% fully" is a misleadingly harsh readout. **Server det does NOT transfer** as a
lever (see "Server det doesn't transfer"): in our pipeline it scores ~81–82% items
at *any* resolution, same as mobile — the cached 93–97% is PaddleOCR-3.3.0-
container-specific and unreproducible by current paddle or us. So **mobile det
stays**; the remaining accuracy lever is **item recall on dense receipts**, not the
detector. (Earlier same-day drafts wrongly called server det "the proven lever" —
that was an artifact of comparing to the 3.3.0 container's output; corrected here.)

**Open next steps (ranked):**
1. **Item recall on dense receipts** (fresh / foody / costco): ~24 header-OK
   receipts land <80% items. Detection recall on small/faint lines is the lever
   (mobile det at higher resize, or recall-oriented DB params), measured by the
   good-enough breakdown in `device_sim`.
2. **Parser latency:** `receipt_core::process_receipt` takes **~7s on a dense
   (~120-line Costco) receipt** (small receipts ~0.4s) — a real on-device UX bug,
   likely super-linear spatial pairing. Shared with desktop.
3. **(Optional) VisionKit-preprocessed inputs** — `device_sim --live` is a
   pessimistic raw-photo lower bound; on-device VisionKit deskews/crops, which may
   help item recall. Validate on the real phone.
4. Housekeeping: rename the bundled rec model to its `en_` name; push the branch to
   `origin`; consider excluding the DEBUG bundled fixture from Release.

CoreML/ANE is **off by default**: measured on a physical iPhone 15 (A16) it makes
the shipped (dynamic-shape) mobile models **~3× slower AND wrong** (see "CoreML is
OFF by default — measured on the iPhone 15" below). The infra stays compiled in
(it drives an on-device A/B toggle), but the shipped path is CPU; the ANE only
ever helped the shelved fixed-shape server det, not these mobile models.

**Key tool:** `cargo run -p ocr-paddle --example device_sim -- <dir-or-img>
[--cached] [--by-merchant] [--detcmp] [--attrib] [--reccached] [--probdump DIR] [--dump]
[--models DIR]`. `OCR_RESIZE_LONG=<n>` overrides the detection resize (default
1536; use 960 to match PaddleOCR). Diagnostics: `--attrib` buckets each live
failure by stage (det-miss / bad-crop / true-rec / pairing — note bad-crop is
*contaminated* by the cached-geometry mismatch, treat with suspicion); `--detcmp`
compares our boxes vs `.ocr.json`; `--reccached` runs OUR rec on the desktop boxes
(invalidated by the cached-frame mismatch — kept for the record); `--probdump DIR`
dumps the raw DB prob map + our boxes for `scripts/contour_cmp.py` (feeds the same
prob map through PaddleOCR's reference DBPostProcess); `REC_DUMP_DIR=<dir>` saves
each line's pre-rec crop PNG + box/conf/text. The ceiling experiments use a local
PaddleOCR venv + `scripts/{contour_cmp,paddle_e2e,manual_server_e2e}.py`. Needs
`models/` populated.

## Box-position hypothesis closed + ceiling finding (2026-06-25)

The 2026-06-24 "detection box positions diverge (imageproc vs OpenCV) → scrambled
pairing" hypothesis is **disproven**. **Root cause: the detection model.** The
cached baseline uses PP-OCRv5 **server det** (the container default for
`lang='en'`); on-device we ship **mobile det**. A local PaddleOCR was used to run
the reference pipeline — both 3.7.0 and the container's pinned **3.3.0** (from
`../beanbeaver-ocr/.venv`). Evidence (same parser/scorer; "text-recall" = expected
item desc+price present anywhere, pairing aside):

| experiment | isolates | result |
|---|---|---|
| `contour_cmp.py` (same prob map → our vs PaddleOCR `DBPostProcess`) | contour/min-rect/unclip | IoU **0.94** → our postprocess ≈ OpenCV |
| `mask_cmp.py` (ours vs paddle det, mobile @960) | our mask (preprocess+ONNX) | IoU **0.84**, no scale drift → our det ≈ paddle **mobile** det |
| `paddle_e2e.py` mobile, full pipeline | our mobile pipeline ceiling | 48% fully / 80% items ≈ our live 46%/82% |
| 3.3.0 **mobile** vs **server**, full pipeline, **corpus** | the detection model | **mobile 79% vs server 93% text-recall (+14)**; dense subset 63% vs 91% (+28) |
| our live (3.7.0-equiv mobile) vs cached (server) | end-to-end | our mobile **83%** vs cached **93%** text-recall |

**What's faithful (NOT the gap):** contours, mask, recognition model, and our whole
mobile pipeline — all reproduce current PaddleOCR-*mobile* end-to-end (paddle_e2e
mobile 48%/80% ≈ our live). ⚠️ An earlier 2026-06-25 draft of this section wrongly
concluded "server det not a lever / the gap is input-image conditioning" — that
came from a flawed **manual** server assembly (`manual_server_e2e.py`, no
integrated pipeline). The integrated full-pipeline comparison below corrects it.

**The gap IS the detection model (server vs mobile).** Same version, same pipeline,
only the detector differs: 3.3.0 server (= cached) **91%** vs 3.3.0 mobile **63%**
on the dense subset; corpus-wide cached(server) **93%** vs our mobile **~83%**.
Proof the cache used server det: 3.3.0 *mobile* on the same images = 63% ≠ cached
91%.

**The input image is NOT degraded** (this was investigated directly). The stored
jpg is the original high-res phone photo (e.g. 1298×5463, EXIF orientation normal),
sharp and legible, **added in the same commit as its `.ocr.json`** — not
re-saved/redacted after caching. Both cache and our pipeline OCR a same-resolution
~813×3100 capped+padded image. The cached boxes not aligning with ours (Y scatter
std ~11px) is explained by **server-det geometry + the container's doc-unwarp**,
not image changes.

**Lever (portable): ship server det on-device.** The macOS bus-error on server det
is a *paddle-CPU* issue, not ONNX/iOS — the earlier on-device experiment already
ran server det as ONNX (`device_sim`) without crashing. Convert
`PP-OCRv5_server_det` to ONNX and re-measure with `device_sim --live`; expect items
~83%→~93%. Cost: ~+80 MB model + slower detection (earlier note: ~3.7× latency),
likely gated on a CoreML/ANE EP. NOTE: the container also runs doc-orientation +
UVDoc dewarp by default; in *3.7.0* dewarp *hurt* (server+dewarp 63% on the subset,
manual assembly), so treat dewarp as unverified — the clean, reproducible lever is
the **server detector**. (This also corrects the stale "bigger models don't fix it"
line: detection capability *is* the axis; the old test was confounded by the
multilingual rec + scorer bug + 960 resize.)

**Why `--reccached` is invalid:** feeding the cached boxes through our recognizer
crops the wrong regions (the cached frame ≠ our image), so it scores ~0 on items
even though, on crops that *do* land on text, our rec is exact (`1968518 WHITE
RABBIT`, `3028018 GREEN ONIONS` matched byte-for-byte). Kept as a mode for the
record; do not trust its score.

**Side finding — parser latency:** `receipt_core::process_receipt` takes ~7s on a
dense Costco receipt (~120 lines) vs ~0.4s on a small one — a real on-device
latency bug (super-linear spatial pairing), shared with desktop.

**Open:** the *exact* mechanism of the cache's better conditioning (deskew vs
UVDoc dewarp vs different image bytes / redaction re-save) — under investigation.

## CoreML / Neural Engine acceleration for server det (2026-06-25)

To make the server-det lever viable on-device (it's ~11× mobile det: ~3.2 s/img
on M1 CPU, est. ~6 s on iPhone 15 CPU), wired up Apple's ML acceleration and
validated it.

**iPhone 15+ has a 16-core Apple Neural Engine** (A16 ~17 TOPS; A17 Pro/A18 ~35
TOPS) plus Metal GPU. **Our shipped iOS `libonnxruntime.a` already contains the
CoreML execution provider** (verified via `nm`; ~11.8k CoreML symbols; no XNNPACK),
and the `ort` crate exposes it behind a `coreml` feature.

**Done (steps 1 + 2):**
1. **CoreML EP wired into the engine** — `crates/ocr-paddle/src/session.rs`
   centralises session construction; det/rec/cls all go through it. Behind the
   `ocr-paddle` `coreml` feature (→ `ort/coreml`), off by default (Linux/desktop
   unaffected), enabled by `build-xcframework.sh` (`COREML=1` default) and
   `bb-receipt-ffi`'s `coreml` feature. Registration is best-effort → graceful CPU
   fallback. Runtime env knobs (no rebuild): `OCR_COREML=0` (force CPU),
   `OCR_COREML_UNITS=ane|gpu|cpu|all` (default ane), `OCR_COREML_FORMAT=
   neuralnetwork|mlprogram` (default **neuralnetwork**), `OCR_COREML_CACHE_DIR=<dir>`
   (cache compiled model across launches). Validated on M1: identical detections
   vs CPU.
2. **Fixed-shape server det ONNX** — CoreML/ANE needs static input shapes, so
   exported `models/PP-OCRv5_server_det_fixed_1536x768.onnx` (input `[1,3,1536,768]`,
   88 MB, gitignored). H=1536 = our resize-long; W=768 letterboxes the widest
   receipt aspect (~1:2) with margin. Regenerate: `paddle2onnx` from
   `~/.paddlex/official_models/PP-OCRv5_server_det` → then `python -m
   onnxruntime.tools.make_dynamic_shape_fixed --input_name x --input_shape
   1,3,1536,768`.

**M1 validation (fixed server det, the iPhone proxy):**

| compute | latency/inference |
|---|---|
| CPU | 2170 ms |
| CoreML **ANE** (NeuralNetwork) | **579 ms** (3.7×) |
| CoreML GPU | 646 ms |
| CoreML **ALL** (ANE+GPU+CPU) | **432 ms** (5.0×) |

261/264 nodes offload to CoreML. ⚠️ **MLProgram format fails** to compile this
model (`MaxPool` `ceil_mode=True` + SAME padding) → default to **NeuralNetwork**.
(Fixing that MaxPool to `ceil_mode=False` would unlock MLProgram, possibly faster —
future option.) On iPhone 15 (A16 ANE ≈ 17 TOPS vs M1 ≈ 11) server det should land
~0.4–0.6 s — vs ~6 s on CPU. **Server det becomes viable with the ANE.**

**Remaining before on-device profiling (step 3, needs the physical iPhone 15):**
- Letterbox preprocessing to feed the fixed 1536×768 canvas (white-pad, preserve
  aspect) — the current `preprocess_det` is variable-shape.
- Bundle the 88 MB model + xcodeproj resource entry; set `OCR_COREML_CACHE_DIR` to
  the app caches dir from Swift (avoids per-launch recompile).
- Profile on the device with `CoreML::with_profile_compute_plan(true)` to confirm
  ANE dispatch + measure real latency; verify accuracy with the letterboxed input.

## CoreML is OFF by default — measured on the iPhone 15 (2026-06-25)

Deployed the metrics build (per-stage timings + an `OCR_COREML` toggle, commit
`d80a728`) to a **physical iPhone 15 (A16)** and ran the bundled Costco sample
with CoreML on vs off — same image, only the execution provider differs.
**CoreML is ~3× slower at every stage AND produces wrong output:**

| stage (ms) | CPU (`OCR_COREML=0`) | CoreML ANE | ratio |
|---|---|---|---|
| detect | 207 | 901 | 4.4× |
| recognize | 544 | 1463 | 2.7× |
| classify | (small) | 1052 | — |
| parse | 174 | ~174 | — |
| **wall (end-to-end)** | **1195** | **3488** | **2.9×** |
| result | **correct** | **wrong** | |

**Root cause:** the shipped det/rec/cls models are **dynamic-shape**. The CoreML
EP can't run those on the ANE cleanly — it re-partitions / recompiles per input
shape (every photo is a different size) and runs the offloaded pieces in **fp16**,
which both costs more than it saves and degrades the detection prob-map /
recognition logits enough to misread. This is the on-device confirmation of the
`session.rs` "dynamic shape → CoreML can't take the node" note, and it matches the
simulator (CoreML on → garbage parse, CPU → correct).

**Decision: default to CPU** — `useCoreML = false` in the app (and `coreMLEnabled`
in `ReceiptPipeline`), i.e. `OCR_COREML=0`. The `coreml` feature stays compiled
into the xcframework so the toggle can still drive an A/B, but the shipped path is
CPU. At beta, consider dropping the `coreml` feature from the Release xcframework
entirely (smaller binary). The CoreML/ANE work isn't wasted — it remains the path
for any future **fixed-shape** detector — it's just shelved for what we ship.

**Bonus finding — the "~7 s parse" may be a debug artifact.** End-to-end CPU
latency on this (medium, ~7-item) Costco receipt is **~1.2 s, with parse only
174 ms** in a Release on-device build. The doc's earlier "~7 s parse on a dense
(~120-line) Costco" was a `device_sim` (default **debug**, unoptimized) Mac
measurement. Before treating parser latency as a real on-device bug, re-measure
the dense ~120-line receipt in a **Release** build on-device — release Rust is
~10–50× faster than debug for this compute, so the real number is likely far
under 7 s. (`device_sim` itself should be run with `--release` for any latency
claim.)

## Server det doesn't transfer + good-enough metric (2026-06-25)

Pulled the server-det lever end-to-end in our pipeline. It **does not transfer**:

- Wired a fixed-shape detection path (`preprocess_det_fixed` letterboxes to a
  static canvas; `detect_fixed` maps boxes back via `(p−pad)/scale`; `Detector`
  auto-detects a static input shape). Ran the fixed server det
  (`PP-OCRv5_server_det_fixed_1536x768.onnx`) live: **82% items / 46% fully** — same
  as mobile.
- Control (dynamic server det at its native **960**, matching the container
  exactly): **81% items / 39% fully** — also no better.

So **our pipeline scores ~81–82% items with server det at any resolution**, equal
to mobile (82%) and current-paddle-mobile (80%). Only the **3.3.0 container** server
det reaches 93–97%; that is version-specific and unreproducible by current paddle
(3.7.0) or our Rust stack. **Conclusion: keep mobile det** (4.6 MB, fast, ANE
headroom). The CoreML/ANE work stands (it's correct + reusable) but server det is
shelved on accuracy, not latency.

**Good-enough metric (the right product readout).** "Fully correct" (100% of items
+ merchant/date/total) is too harsh for a scan-assist UX where the user can add a
missed line. `device_sim`'s summary now also reports, per corpus:
- **header-correct** = merchant + date + total all right (the CC-matching keys),
- **good enough** = header-correct **and** items ≥ 80% (and a ≥90% variant),
- mean per-receipt item recall, and a not-full breakdown (miss-items-only vs
  miss-a-header-field).

`--by-merchant` groups the corpus by canonical merchant and prints the same
readouts, surfacing which merchants already reach 100% (easy) vs which dense
layouts drag recall. Cached (desktop OCR) hits ~100% fully on nearly every
merchant — so per-merchant 100% is reachable with better OCR; the mobile gaps are
OCR quality, not the parser.

Mobile client (shipped) on the 80-case verified corpus:

| readout | mobile live |
|---|---|
| merchant+date+total all OK (header) | **74/80 (92%)** |
| good enough (header + items ≥80%) | **50/80 (62%)** |
| good enough (header + items ≥90%) | 39/80 (49%) |
| fully OK (header + items =100%) | 37/80 (46%) |
| mean per-receipt item recall | 82% |

Of the 43 imperfect receipts, **37 get merchant/date/total right and only miss some
items; just 6 miss a header field.** So 92% of scans nail the matching keys and 62%
are good-enough; the lever is item recall on the ~24 header-OK receipts below 80%
items (dense receipts).

## Locked decisions

- **On-device PaddleOCR (PP-OCRv5)** — the **English** recognition model
  (`en_PP-OCRv5_mobile_rec`, 436-class), matching the desktop `beanbeaver-ocr`
  service (`lang="en"` → the same English model). The target receipts are
  bilingual; both pipelines ignore the CJK column and read the English/numeric
  text. (We initially mis-shipped the 18,383-class multilingual rec; corrected
  2026-06-24 — see "Recognition model mismatch".) Detection + textline-orientation
  are the PP-OCRv5 mobile models. Output is *close*, not byte-identical (see
  "Parity").
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

## Model setup (models are committed to git)

NOTE: contrary to an earlier version of this doc, the ONNX models in `models/`
**are tracked in git** (despite the `models/` `.gitignore` entry — they were
force-added). This is a deliberate early-dev convenience for switching between the
Linux workstation and the M1 Mac; a normal checkout has them and needs **no setup**
to build/run. (Revisit at beta for a proper large-file strategy.) The desktop
service uses **PaddleOCR 3.3.0, PP-OCRv5, `lang='en'`,
`use_textline_orientation=True`**; the on-device models match it:

- `PP-OCRv5_mobile_det.onnx` — detection (~4.8 MB)
- `PP-OCRv5_mobile_rec.onnx` — recognition; holds the **English**
  `en_PP-OCRv5_mobile_rec` weights (436-class, ~7.9 MB) under this filename to
  avoid churn in `crates/ffi`/`process.rs`/`phase5_e2e`/the xcodeproj (rename at
  beta). See "Recognition model mismatch".
- `PP-LCNet_x1_0_textline_ori.onnx` — textline orientation (~6.7 MB)

To **regenerate/upgrade** them (paddlepaddle needs Python 3.12):

```bash
python3.12 -m venv /tmp/p2o && source /tmp/p2o/bin/activate
pip install --upgrade pip && pip install paddlepaddle paddle2onnx packaging

# rec (ENGLISH): from the PaddleX cache, populated by running the desktop OCR once
paddle2onnx --model_dir ~/.paddlex/official_models/en_PP-OCRv5_mobile_rec \
  --model_filename inference.json --params_filename inference.pdiparams \
  --save_file models/PP-OCRv5_mobile_rec.onnx --opset_version 14

# det + textline-ori: official PP-OCRv5 mobile tarballs
cd models
for u in PP-OCRv5_mobile_det PP-LCNet_x1_0_textline_ori; do
  wget "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/${u}_infer.tar"
done
for t in *.tar; do tar xf "$t"; done
for m in PP-OCRv5_mobile_det PP-LCNet_x1_0_textline_ori; do
  paddle2onnx --model_dir "${m}_infer" --model_filename inference.json \
    --params_filename inference.pdiparams --save_file "${m}.onnx" --opset_version 14
done
deactivate
```

The recognition dictionary is committed:
`crates/ocr-paddle/assets/en_ppocrv5_rec_dict.txt` (436-char English). The
18,383-char multilingual `ppocrv5_rec_dict.txt` is retained for a future opt-in
CJK build.

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

## Recognition model mismatch + en swap (2026-06-24) — CURRENT LIVE NUMBERS

The single biggest on-device lever turned out to be the **recognition model
choice**, not our port or the detection contours. The desktop baseline runs
`lang="en"` → **`en_PP-OCRv5_mobile_rec`** (436-class Latin CTC head); on-device
we had shipped the **18,383-class multilingual `PP-OCRv5_mobile_rec`**. Evidence
the desktop uses the English model: **0/80** cached `.ocr.json` snapshots contain
any CJK, while our multilingual model emitted CJK (`猪簡`) on the same receipts;
the cached rec dict is 436 Latin chars, ours was 18,383 (CJK).

Why it matters: a 42×-larger output vocabulary mis-classifies digits/punctuation
on clean crops. Localized with the `REC_DUMP_DIR` crop probe on three killers:
- `bestco 184.64 → 1841.64`: **clean crop**, model inserted a digit (pure rec).
- `tnt date 2026 → 0263`: digits read right but a **dropped space** (`26`+`3` →
  `263`) made the parser read `263` as the year (rec space + parse).
- `costco 72.41 → 2.41`: leading `7` **clipped** by a bad warp crop (box geometry,
  not rec).

Fix: switch the on-device rec to the same English model (convert
`en_PP-OCRv5_mobile_rec`, ship its 436-char dict, decode against it). These are
bilingual Canadian-Chinese grocery receipts; the desktop ignores the CJK column
and reads the English/numerics, and every fixture is scored on that.

`device_sim` live (80-receipt corpus), same parser + scorer, multilingual → en:

| metric | live (multilingual) | live (en) | cached baseline | gap |
|---|---|---|---|---|
| merchant | 90% | **98%** | 100% | −2 |
| date | 88% | **100%** | 100% | **0** |
| total | 82% | **90%** | 99% | −9 |
| critical items | 61% | **82%** | 97% | −15 |
| fully correct | 24% | **44%** | 89% | −45 |

Date is fully closed; fully-correct nearly doubled. The remaining gap is now
**box-position / bad-crop on the total line** (8 `0.00`/partial total misreads,
mostly Costco/NoFrills) plus item coverage on dense receipts — i.e. the
`db_postprocess.rs` lever, *not* recognition. This also retires the old "bigger
models don't fix it" framing as the wrong axis: the win was a *narrower* (English)
model, not a bigger one.

## Total reconciliation against the payment block (2026-06-24)

Dissecting the 8 remaining live total-fails (all Costco/NoFrills) showed they are
**not OCR failures** — the correct total is recognized, but a box-Y
mis-registration scrambles the label↔amount pairing so the grouper hands
`extract_total` a line like `TOTAL 20.14` (the tax row) with the real `245.87`
orphaned on the next line. The dump for `costco_245_87`:

```
SUBTOTAL 9.69      (should be 225.73)
TAX 225.73         (should be 20.14)
TOTAL 20.14        (should be 245.87)   <- extract_total returns 20.14
245.87             (orphaned; also in "AMOUNT: 245.87" and "MasterCard 245.87")
```

Fix (`receipt_fields::extract_total`): after the raw label-scan pick, **reconcile
against the payment block** — prefer an amount corroborated by ≥2 payment lines
(card tender / `AMOUNT:` echo) but only when it **exceeds** the candidate, so
cash-with-change and split-tender receipts are untouched and correctly-paired
receipts never trigger it. Shared with desktop via the PyO3 binding;
parity-safe by construction and verified (cached corpus byte-identical).

| metric | live (en) | live (en + reconcile) | cached |
|---|---|---|---|
| total | 90% | **95%** (76/80) | 99% |
| fully correct | 44% | **46%** | 89% |

Recovers the 4 Costco totals. The 4 remaining total-fails read `0.00` (fail-loud
→ flagged for confirmation in matching, see the `total-matching-safeguard`
memory; 1 is a `known_failures`). The non-Costco residual (C&C / NoFrills, no
`AMOUNT:` echo) and the item-pairing bucket fall to the box-position lever.

## Scorer fix + corrected baseline (2026-06-24) — CACHED BASELINE

`device_sim`'s scorer (and `phase5_e2e.rs`) ignored the `expected.json`
`category_optional` flag — it always enforced the category, even on items the
test marks category-don't-care (items even the desktop pipeline mis-categorizes).
The Python e2e harness honors the flag (`test_e2e_receipts.py`). Both Rust scorers
were fixed to honor it. Corrected numbers (mobile det@1536, 80-receipt corpus):

| metric | live (on-device) | cached (= desktop-honest baseline) |
|---|---|---|
| merchant | 90% | 100% |
| date | 88% | 100% |
| total | 82% | 99% |
| critical items | **61%** (396/644) | **97%** (622/644) |
| fully correct | **24%** (19/80) | **89%** (71/80) |

**Two findings:**
- **The old cached "84%/95%" was measurement error, not parser drift.** The 4
  "residual" fixtures (`c_c_supermarket`, `costco_245_87`, `bestco_20260204c`,
  `cnc_20260130`) failed only on 7 `category_optional` items whose desc+price the
  Rust parser got *right*. With the flag honored, cached = **71/80 = 89%**, and the
  only failures left are the 9 desktop `known_failures` — i.e. the Rust parser is
  **71/71 faithful** on every fixture that should pass. **Cached now ==
  desktop-honest.** This answers the prior Step-1 question ("is `device_sim
  --cached` under-measuring?"): yes — via `known_failures` masking in pytest + the
  `category_optional` scorer bug; both now accounted for.
- **The fix did NOT move live.** It only rescues items with correct desc+price but
  wrong category; on degraded live OCR those items fail upstream on desc/price, so
  the leniency never applies. Net: the live→cached gap *widened* (fully −60→−65,
  items −34→−36), reinforcing that the on-device OCR stage is the entire bottleneck.

## On-device OCR quality (measured 2026-06-21, private corpus, 80 receipts)

> ⚠️ Superseded by the 2026-06-24 table above — the cached column here (95%/84%)
> predates the `category_optional` scorer fix, and the live column predates the
> 1536 detect-resize win. Kept for history.

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

**Bigger weights are NOT the fix** — best heavy config reaches 26% vs the 89% the
same parser gets on desktop OCR (this table predates the 2026-06-24 scorer fix, so
it shows the cached column as 84%/95%; corrected to 89%/97%), at +80–160 MB and
~3.7× latency (18.8 s/img on Mac CPU), and they even regress date. The bottleneck is **our Rust OCR pipeline,
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
- **`unclip` is already faithful** (distance = area·ratio/perimeter, grow rect
  per side = pyclipper round-offset of a rect) — verified, not a lever.
- **Detect-vs-recog split** (`--detcmp` text recall, dense subset): box-recall
  63% but **text recall 76%** — our box *positions* diverge from PaddleOCR (the
  center metric understated us), yet item-extraction (61%) < text-recall (76%),
  so positional divergence degrades receipt-core's spatial item↔price pairing.
- **Can we fully reproduce desktop on-device? Unlikely** — irreducible gap from
  a different contour lib (`imageproc` Suzuki vs OpenCV), float kernels,
  recognition misreads, and possible PaddleOCR doc-orientation. Realistic to
  keep closing it, not to hit byte-parity. Remaining levers, measured:
  (1) **recognition accuracy** — date/digit garbling (`0263`, `3014`, `C0KE`)
  looks like a fixable rec-preprocess/CTC issue, likely highest value;
  (2) **box positional fidelity** — helps the parser pair items/prices;
  (3) detection recall on the ~24% missed lines (diminishing returns).
  Max-accuracy fallback remains the proven server/hybrid path.

## Desktop pipeline, stage-by-stage (master + ../beanbeaver-ocr)

`../beanbeaver-ocr` is a thin wrapper: `PaddleOCR(use_textline_orientation=True,
lang='en', ocr_version='PP-OCRv5', device='cpu').ocr(img)`. Per-model behavior is
pinned by each model's `inference.yml` (we have them locally). Master owns the
image conditioning before and the parse after.

| Stage | Desktop | Our `ocr-paddle` | Verdict |
|---|---|---|---|
| Pre-OCR (`resize_image_bytes`/`image_pipeline.py`) | EXIF → **deskew** (BICUBIC, white) → resize LANCZOS cap 3000 → pad 50 → JPEG-95 | `resize_and_pad`: Lanczos cap 3000 → pad 50; no EXIF/deskew | resize+pad ✓; **deskew missing** |
| Doc orientation | `use_doc_orientation_classify/unwarp` not set → off | none | ✓ not a gap |
| Det resize | `DetResizeForTest resize_long 960`, round 128 | `RESIZE_LONG 1536`, round 32 | deliberate (helps); rounding minor |
| DB postprocess | thresh .3/box .6/unclip 1.5; cv2 contours; minAreaRect; pyclipper | same params; imageproc contours; geo min-rect; analytic unclip | params+unclip ✓; **contours → box positions diverge** |
| Textline-ori cls | PP-LCNet_x1_0_textline_ori | `classify.rs` | ✓ present |
| Rec preprocess | `RecResizeImg` h48/dyn-W, `(x/255−.5)/.5` BGR | identical | ✓ **faithful** |
| CTC decode | `CTCLabelDecode` greedy; en model: blank0/dict1–436/space437 | identical | ✓ **faithful** (both now use the en rec model) |
| Post-OCR | `transform_paddleocr_result` → `parse_receipt` | `process_receipt` (same receipt-core) | ✓ identical |

**Conclusions:**
- **Recognition + parsing are already faithful** — digit garbling (`0263`, `C0KE`)
  is bad *inputs* to rec (misaligned crops / un-deskewed text), not a rec bug.
- The two real Bucket-B gaps are **upstream of rec**: (a) missing **deskew**,
  (b) **detection box positions** (imageproc vs OpenCV contours; box-recall 63%
  vs text-recall 76%).
- **`device_sim`'s 61% is a pessimistic lower bound for real iOS**: desktop
  deskews its inputs in software; on-device, **VisionKit already deskews/crops/
  orients** before our pipeline.

**Deskew quantified (disproven as a lever):** ran master's deskew (EXIF + the
projection-profile rotate) over the corpus, then `device_sim`. Only 13/80 images
were skewed enough to rotate, and the aggregate barely moved (items 61%→60%,
fully 24%→24%, total 82%→86%). So **deskew does not explain the gap** on this
(mostly-straight) corpus — no need to port it. The dominant failures are
single high-value lines: **total** (`0.00`, `2.41` for 72.41, `1841.64` for
184.64) and **date** (`2006`/`0263`/`3014` for 2026) — traceable to detection
box positions feeding slightly-off crops to the (faithful) recognizer.

**Crash bug found + fixed** (`467a0d6`): `String::truncate(80)` in
`receipt_text`/`receipt_spatial` panicked at a non-char-boundary on CJK receipt
text (Python's `[:80]` is char-based). Real iOS crash risk; surfaced by the
deskew run. Fixed to truncate by characters; `receipt-core` tests green.

**Remaining lever = detection box-position fidelity** (the contour→min-box step),
which gates both item coverage and total/date recognition. Deep, partly Bucket A.

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
- **Rec vocabulary** (current English model `en_PP-OCRv5_mobile_rec`):
  `num_classes = blank(0) + dict(436) + space`. `class_char` derives the space
  index from the model's output width at runtime, so it adapts if the dict
  changes. (The retired multilingual model was `blank(0) + dict(18383) + space` =
  18385.)
