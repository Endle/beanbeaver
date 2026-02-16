#!/usr/bin/env bash
set -euo pipefail

# Run relative to this script's directory so it works from any cwd.

REPO_URL="https://github.com/Endle/beanbeaver.git"
CLONE_PATH="beanbeaver"

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
ls
git submodule add "${REPO_URL}" "${CLONE_PATH}"
cp "${CLONE_PATH}"/flake.nix .
git add flake.nix
git commit -m "Add flake"

# 3) In nix develop, run bb --help.
nix develop -c bb --help

