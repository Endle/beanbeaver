#!/usr/bin/env bash
#
# Build → install → launch → screenshot the BeanBeaverScan app on a booted
# simulator, then print the screenshot path. This is the visual feedback loop
# for iterating on SwiftUI: edit a view, run this, look at the PNG.
#
#   ios/sim-shot.sh                 # build, launch idle, screenshot
#   ios/sim-shot.sh --sample        # also pass -autoRunSample (real on-device OCR)
#   ios/sim-shot.sh --no-build      # skip xcodebuild (reuse last build)
#   ios/sim-shot.sh -o /tmp/x.png   # choose the output path
#
# Requires a booted simulator (e.g. `xcrun simctl boot 'iPhone 17 Pro'` or just
# open Simulator.app). SwiftUI #Preview blocks render in Xcode's canvas, not
# here — this captures the *running* app, which is the only headless path.
set -euo pipefail

SCHEME="BeanBeaverScan"
BID="com.beanbeaver.BeanBeaverScan"
DEST='platform=iOS Simulator,name=iPhone 17 Pro'
OUT="${TMPDIR:-/tmp}/beanbeaver-sim.png"
BUILD=1
LAUNCH_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sample)   LAUNCH_ARGS+=(-autoRunSample); shift ;;
    --no-build) BUILD=0; shift ;;
    -o)         OUT="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/BeanBeaverScan"

if [[ "$BUILD" == 1 ]]; then
  echo "── build ──────────────────────────────────────────"
  xcodebuild -scheme "$SCHEME" -sdk iphonesimulator \
    -destination "$DEST" -configuration Debug build \
    | tail -2
fi

APP=$(xcodebuild -scheme "$SCHEME" -sdk iphonesimulator -configuration Debug \
        -showBuildSettings 2>/dev/null \
      | awk -F' = ' '/ BUILT_PRODUCTS_DIR =/{d=$2} / FULL_PRODUCT_NAME =/{n=$2} END{print d"/"n}')

echo "── install & launch ───────────────────────────────"
xcrun simctl install booted "$APP"
xcrun simctl terminate booted "$BID" 2>/dev/null || true
xcrun simctl launch booted "$BID" ${LAUNCH_ARGS[@]+"${LAUNCH_ARGS[@]}"}

# Give SwiftUI a beat (longer when -autoRunSample runs OCR).
if [[ " ${LAUNCH_ARGS[*]-} " == *-autoRunSample* ]]; then sleep 12; else sleep 2; fi

xcrun simctl io booted screenshot "$OUT" >/dev/null 2>&1
echo "── screenshot ─────────────────────────────────────"
echo "$OUT"
