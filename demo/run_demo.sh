#!/usr/bin/env bash
set -euo pipefail

# Run relative to this script's directory so it works from any cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

SUBMODULE_URL="https://github.com/Endle/beanbeaver.git"
SUBMODULE_PATH="beanbeaver"

if [[ -e "${SUBMODULE_PATH}" ]]; then
  echo "Path '${SUBMODULE_PATH}' already exists. Skipping submodule add."
else
  git submodule add "${SUBMODULE_URL}" "${SUBMODULE_PATH}"
fi

cp "${SUBMODULE_PATH}"/flake.nix .
nix develop -c bb --help

