#!/usr/bin/env bash
#
# Standard accuracy + latency scorecard for the on-device OCR pipeline.
# Always builds --release (debug latency is ~10-50× inflated and not trustworthy).
#
#   crates/ocr-paddle/scripts/scorecard.sh                 # live, private corpus
#   crates/ocr-paddle/scripts/scorecard.sh <corpus-dir>    # live, custom corpus
#   crates/ocr-paddle/scripts/scorecard.sh '' --cached     # cached (server OCR) baseline
#   crates/ocr-paddle/scripts/scorecard.sh '' --by-merchant
#
# Any extra args after the corpus dir are passed straight to device_sim.
set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Default corpus: the private 80-receipt set (sibling repo). Override with $1.
CORPUS="${1:-../beanbeaver-private-test/receipts_e2e}"
shift || true

if [ ! -e "$CORPUS" ]; then
  echo "error: corpus not found: $CORPUS" >&2
  echo "  pass a dir as the first arg, or clone the private test set to ../beanbeaver-private-test" >&2
  exit 2
fi

exec cargo run --release -q -p ocr-paddle --example device_sim -- "$CORPUS" "$@"
