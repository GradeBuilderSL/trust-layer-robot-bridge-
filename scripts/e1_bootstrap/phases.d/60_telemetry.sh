#!/usr/bin/env bash

if [ "${E1_BOOTSTRAP_START_STACK}" = "1" ]; then
    bootstrap_log "telemetry already captured by start_e1_stack.sh"
    exit 0
fi

if [ "${E1_BOOTSTRAP_COLLECT_TELEMETRY}" != "1" ]; then
    bootstrap_log "skip telemetry snapshot"
    exit 0
fi

python3 "${BOOTSTRAP_REPO_ROOT}/scripts/e1_collect_telemetry.py" \
    --robot-url "${ROBOT_URL:-http://127.0.0.1:8083}" \
    --bridge-url "http://127.0.0.1:${BRIDGE_PORT:-8080}" \
    --wait-sec "${E1_BOOTSTRAP_WAIT_SEC}" \
    --output-dir "${BOOTSTRAP_REPO_ROOT}/runtime/e1/reports"
