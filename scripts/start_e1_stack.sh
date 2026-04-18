#!/usr/bin/env bash
# Starts the full E1 stack in the background and captures a telemetry snapshot.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_DIR="${REPO_ROOT}/runtime/e1"
LOG_DIR="${RUNTIME_DIR}/logs"
PID_DIR="${RUNTIME_DIR}/pids"
REPORT_DIR="${RUNTIME_DIR}/reports"

mkdir -p "${LOG_DIR}" "${PID_DIR}" "${REPORT_DIR}"
cd "${REPO_ROOT}"

if [ -f "${REPO_ROOT}/.env.e1.local" ]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env.e1.local"
    set +a
fi

export E1_SERVER_PORT="${E1_SERVER_PORT:-8083}"
export BRIDGE_PORT="${BRIDGE_PORT:-8080}"
export ROBOT_IP="${ROBOT_IP:-127.0.0.1}"
export ROBOT_PORT="${ROBOT_PORT:-${E1_SERVER_PORT}}"
export ROBOT_URL="${ROBOT_URL:-http://${ROBOT_IP}:${ROBOT_PORT}}"
WAIT_SEC="${E1_BOOTSTRAP_WAIT_SEC:-20}"

pid_running() {
    local pid_file="$1"
    [ -f "${pid_file}" ] || return 1
    local pid
    pid="$(cat "${pid_file}")"
    [ -n "${pid}" ] || return 1
    kill -0 "${pid}" 2>/dev/null
}

start_bg() {
    local name="$1"
    local pid_file="$2"
    local log_file="$3"
    shift 3

    if pid_running "${pid_file}"; then
        echo "${name} already running (pid $(cat "${pid_file}"))"
        return 0
    fi

    nohup "$@" >"${log_file}" 2>&1 &
    echo $! > "${pid_file}"
    echo "started ${name} (pid $!, log ${log_file})"
}

start_bg \
    "e1_server" \
    "${PID_DIR}/e1_server.pid" \
    "${LOG_DIR}/e1_server.log" \
    env E1_SERVER_PORT="${E1_SERVER_PORT}" ROBOT_URL="${ROBOT_URL}" bash "${REPO_ROOT}/scripts/start_e1_server.sh"

sleep 2

start_bg \
    "bridge.main" \
    "${PID_DIR}/bridge.pid" \
    "${LOG_DIR}/bridge.log" \
    env BRIDGE_PORT="${BRIDGE_PORT}" ROBOT_URL="${ROBOT_URL}" ADAPTER_TYPE="${ADAPTER_TYPE:-e1}" bash "${REPO_ROOT}/scripts/start_e1_bridge.sh"

python3 "${REPO_ROOT}/scripts/e1_collect_telemetry.py" \
    --robot-url "${ROBOT_URL}" \
    --bridge-url "http://127.0.0.1:${BRIDGE_PORT}" \
    --wait-sec "${WAIT_SEC}" \
    --output-dir "${REPORT_DIR}"
