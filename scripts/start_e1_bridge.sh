#!/usr/bin/env bash
# start_e1_bridge.sh — launch the Trust Layer bridge against a Noetix E1.
#
# Run on the operator workstation (or on the Jetson Orin Nano Super itself,
# in which case set ROBOT_URL=http://127.0.0.1:8083). The e1_server.py
# REST wrapper must already be running on E1's Jetson — see start_e1_server.sh.
#
# Usage:
#   bash scripts/start_e1_bridge.sh                       # default IP
#   ROBOT_URL=http://10.0.0.42:8083 bash scripts/start_e1_bridge.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."

export ADAPTER_TYPE="e1"
export ROBOT_URL="${ROBOT_URL:-http://192.168.55.101:8083}"
export ROBOT_NAME="${ROBOT_NAME:-Noetix E1}"
export ROBOT_ID="${ROBOT_ID:-e1-01}"
export ROBOT_MODEL="${ROBOT_MODEL:-Noetix E1}"
export BRIDGE_PORT="${BRIDGE_PORT:-8080}"
export WORKSTATION_URL="${WORKSTATION_URL:-http://localhost:8888}"
export WATCHDOG_TIMEOUT_MS="${WATCHDOG_TIMEOUT_MS:-2000}"

echo "═══════════════════════════════════════════════"
echo "  Trust Layer Bridge → Noetix E1"
echo "  ROBOT_URL = ${ROBOT_URL}"
echo "  PORT      = ${BRIDGE_PORT}"
echo "═══════════════════════════════════════════════"

# Sanity check: e1_server reachable?
if command -v curl >/dev/null 2>&1; then
    if ! curl -s -m 2 "${ROBOT_URL}/health" >/dev/null; then
        echo "WARN: ${ROBOT_URL}/health did not respond."
        echo "      Make sure e1_server.py is running on the Jetson:"
        echo "        ssh noetix@192.168.55.101"
        echo "        cd /opt/trust-layer-bridge && python3 -m bridge.e1_server"
    fi
fi

exec python3 -m bridge.main
