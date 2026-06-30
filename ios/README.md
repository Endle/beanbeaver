# BeanBeaver iOS (prototype)

On-device receipt scanner: pick a photo → PP-OCRv5 OCR + parse + categorize →
beancount, all in Rust via a UniFFI seam. See `../docs/ios_port.md` for the plan.

## Layout

```
ios/
  BBReceiptKit/                 local SPM package wrapping the Rust core
    Package.swift               binaryTarget(xcframework) + Swift target
    Sources/BBReceiptKit/
      ReceiptScanner.swift      committed conveniences over the FFI
      Generated/…               ⚙️ generated UniFFI Swift glue (git-ignored)
    Frameworks/
      BBReceiptFFI.xcframework  ⚙️ built (device + sim slices, git-ignored)
  BeanBeaverScan/               the SwiftUI app
    BeanBeaverScan.xcodeproj
    BeanBeaverScan/             App / ContentView / ReceiptPipeline
```

⚙️ = produced by `../crates/ffi/build-xcframework.sh`; not committed.

## Build steps

```bash
# 1. Build the Rust core into the SPM package (xcframework + Swift glue).
#    Needs models/ populated (see ../docs/ios_port.md "Model setup").
crates/ffi/build-xcframework.sh            # PROFILE=debug for faster iteration

# 2. One-time: install the iOS platform/simulator runtime (~7 GB).
xcodebuild -downloadPlatform iOS

# 3. Build the app for the simulator.
cd ios/BeanBeaverScan
xcodebuild -scheme BeanBeaverScan -sdk iphonesimulator \
  -destination 'platform=iOS Simulator,name=iPhone 16' build
```

Or just open `BeanBeaverScan.xcodeproj` in Xcode and run.

## Iterating on the UI

- **In Xcode:** `ContentView.swift` ships `#Preview` blocks (DEBUG-only) for the
  result view (full / minimal) and the whole screen in every state
  (idle / scanning / done / failed), backed by mock `ReceiptResult`s — no OCR
  needed. `ReceiptPipeline.preview(_:)` pins a status; `ContentView(previewPipeline:)`
  injects it. Edit a view and the canvas updates live.
- **Headless screenshot:** `ios/sim-shot.sh` builds, installs, launches on the
  booted simulator, and writes a PNG (add `--sample` for a real on-device OCR
  run via `-autoRunSample`). Previews render only in Xcode's canvas, so this is
  the way to capture the running app from the command line.

## Notes

- The 3 `.onnx` models are bundled as app resources (referenced from
  `../../models/`) and loaded via `OcrSession.load(modelsDirectory:)` using
  `Bundle.main.resourceURL`.
- The xcframework's simulator slice is **arm64-only** (Apple-Silicon Macs). Set
  `INCLUDE_X86_SIM=1` on the build script to add an Intel-sim slice.
- `SWIFT_VERSION = 5.0` deliberately: avoids Swift 6 strict-concurrency errors
  on the UniFFI object passed across `Task.detached`.
