#!/usr/bin/env bash
# start_e1_bridge.sh - launch Trust Layer bridge against a Noetix E1.

set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f "${PWD}/.env.e1.local" ]; then
    set -a
    # shellcheck disable=SC1091
    source "${PWD}/.env.e1.local"
    set +a
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TRUST_LAYER_LIVE_ENV="${REPO_ROOT}/../trust-layer/deployments/live/.env.live"

load_env_file() {
    local env_path="$1"
    [ -f "$env_path" ] || return 0
    set -a
    # shellcheck disable=SC1090
    source "$env_path"
    set +a
}

load_env_file "$TRUST_LAYER_LIVE_ENV"

export ADAPTER_TYPE="${ADAPTER_TYPE:-e1}"

if [ -z "${ROBOT_URL:-}" ]; then
    export ROBOT_IP="${ROBOT_IP:-127.0.0.1}"
    export ROBOT_PORT="${ROBOT_PORT:-8083}"
    export ROBOT_URL="http://${ROBOT_IP}:${ROBOT_PORT}"
fi

export ROBOT_NAME="${ROBOT_NAME:-Noetix E1}"
export ROBOT_ID="${ROBOT_ID:-e1-01}"
export ROBOT_MODEL="${ROBOT_MODEL:-Noetix E1}"
export BRIDGE_PORT="${BRIDGE_PORT:-8080}"
export WORKSTATION_URL="${WORKSTATION_URL:-http://localhost:8888}"
export WATCHDOG_TIMEOUT_MS="${WATCHDOG_TIMEOUT_MS:-2000}"

echo "================================================="
echo "  Trust Layer Bridge -> Noetix E1"
echo "  ROBOT_URL = ${ROBOT_URL}"
echo "  PORT      = ${BRIDGE_PORT}"
echo "================================================="

if command -v curl >/dev/null 2>&1; then
    if ! curl -s -m 2 "${ROBOT_URL}/health" >/dev/null; then
        echo "WARN: ${ROBOT_URL}/health did not respond."
        echo "      Start e1_server first, for example:"
        echo "        cd /home/noetix/trust-layer-robot-bridge-"
        echo "        E1_TRANSPORT=noetix_dds bash scripts/start_e1_server.sh"
    fi
fi

exec python3 -m bridge.main
