#!/usr/bin/env bash
set -euo pipefail

# Run relative to this script's directory so it works from any cwd.

CLONE_PATH="beanbeaver"
SOURCE_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 1) Set up a demo beancount directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"
mkdir -p /tmp/dev
WORK_DIR="$(mktemp -d /tmp/dev/work.XXXXXX)"
DEMO_COPY_DIR="${WORK_DIR}/demo"
cp -a "${SCRIPT_DIR}" "${DEMO_COPY_DIR}"
cd "${DEMO_COPY_DIR}"
ls main.beancount >/dev/null || exit 1

git init
git add .
git commit -a -m "set demo ledger"

# 2) Set up beanbeaver
command -v pixi >/dev/null || {
  echo "pixi is required to run this demo." >&2
  exit 1
}

git -c protocol.file.allow=always submodule add "${SOURCE_REPO_ROOT}" "${CLONE_PATH}"
git commit -m "Add beanbeaver"

# 3) In the beanbeaver Pixi environment, run bb --help.
pixi run --manifest-path "${CLONE_PATH}/pixi.toml" bb --help
