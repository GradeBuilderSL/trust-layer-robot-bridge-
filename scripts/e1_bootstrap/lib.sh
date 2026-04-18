#!/usr/bin/env bash

set -euo pipefail

BOOTSTRAP_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOTSTRAP_REPO_ROOT="$(cd "${BOOTSTRAP_SCRIPT_DIR}/../.." && pwd)"
BOOTSTRAP_ENV_FILE="${BOOTSTRAP_REPO_ROOT}/.env.e1.local"
BOOTSTRAP_ENV_EXAMPLE="${BOOTSTRAP_REPO_ROOT}/.env.e1.local.example"
BOOTSTRAP_PHASE_DIR="${BOOTSTRAP_SCRIPT_DIR}/phases.d"

bootstrap_init() {
    cd "${BOOTSTRAP_REPO_ROOT}"

    if [ ! -f "${BOOTSTRAP_ENV_FILE}" ] && [ -f "${BOOTSTRAP_ENV_EXAMPLE}" ]; then
        cp "${BOOTSTRAP_ENV_EXAMPLE}" "${BOOTSTRAP_ENV_FILE}"
        echo "[bootstrap] created ${BOOTSTRAP_ENV_FILE} from example"
    fi

    if [ -f "${BOOTSTRAP_ENV_FILE}" ]; then
        set -a
        # shellcheck disable=SC1090
        source "${BOOTSTRAP_ENV_FILE}"
        set +a
    fi

    export BOOTSTRAP_REPO_ROOT
    export BOOTSTRAP_SCRIPT_DIR
    export BOOTSTRAP_ENV_FILE
    export BOOTSTRAP_PHASE_DIR
    export E1_BOOTSTRAP_INSTALL_DEPS="${E1_BOOTSTRAP_INSTALL_DEPS:-1}"
    export E1_BOOTSTRAP_BUILD_HELPER="${E1_BOOTSTRAP_BUILD_HELPER:-1}"
    export E1_BOOTSTRAP_START_STACK="${E1_BOOTSTRAP_START_STACK:-1}"
    export E1_BOOTSTRAP_COLLECT_TELEMETRY="${E1_BOOTSTRAP_COLLECT_TELEMETRY:-1}"
    export E1_BOOTSTRAP_INSTALL_SPEECH="${E1_BOOTSTRAP_INSTALL_SPEECH:-0}"
    export E1_BOOTSTRAP_WAIT_SEC="${E1_BOOTSTRAP_WAIT_SEC:-20}"

    echo "[bootstrap] repo root: ${BOOTSTRAP_REPO_ROOT}"
}

bootstrap_find_sdk_root() {
    local candidates=()
    if [ -n "${E1_SDK_ROOT:-}" ]; then
        candidates+=("${E1_SDK_ROOT}")
    fi
    candidates+=(
        "${BOOTSTRAP_REPO_ROOT}/../noetix_sdk_e1"
        "${BOOTSTRAP_REPO_ROOT}/noetix_sdk_e1"
        "/opt/noetix_sdk_e1"
        "/opt/trust-layer-bridge/noetix_sdk_e1"
        "/home/noetix/noetix_sdk_e1"
    )

    local path=""
    for path in "${candidates[@]}"; do
        [ -n "${path}" ] || continue
        if [ -f "${path}/config/dds.xml" ]; then
            printf '%s\n' "${path}"
            return 0
        fi
    done
    return 1
}

bootstrap_log() {
    echo "[bootstrap] $*"
}

bootstrap_run_phase() {
    local phase="$1"
    bootstrap_log "phase $(basename "${phase}")"
    # shellcheck disable=SC1090
    source "${phase}"
}

bootstrap_run_all() {
    local phase=""
    for phase in "${BOOTSTRAP_PHASE_DIR}"/*.sh; do
        [ -f "${phase}" ] || continue
        bootstrap_run_phase "${phase}"
    done
}
