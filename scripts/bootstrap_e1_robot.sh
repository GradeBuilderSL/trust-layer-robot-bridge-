#!/usr/bin/env bash
# Entry point for the modular E1 bootstrap package.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOTSTRAP_ROOT="${SCRIPT_DIR}/e1_bootstrap"

# shellcheck disable=SC1091
source "${BOOTSTRAP_ROOT}/lib.sh"

bootstrap_init
bootstrap_run_all

echo "[bootstrap] done"
