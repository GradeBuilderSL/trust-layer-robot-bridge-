#!/usr/bin/env bash
# start_e1_server.sh - launch the Noetix E1 low-level HTTP server on Jetson.
#
# Run this on E1's Jetson Orin, not on the RK3588S motion-control board.

set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f "${PWD}/.env.e1.local" ]; then
    set -a
    # shellcheck disable=SC1091
    source "${PWD}/.env.e1.local"
    set +a
fi

export E1_SERVER_PORT="${E1_SERVER_PORT:-8083}"
export E1_TRANSPORT="${E1_TRANSPORT:-auto}"
export E1_NETWORK_IFACE="${E1_NETWORK_IFACE:-eth0}"
export E1_ROBOT_ID="${E1_ROBOT_ID:-e1-01}"
export E1_ROBOT_NAME="${E1_ROBOT_NAME:-Noetix E1}"

find_sdk_root() {
    local candidates=()
    if [ -n "${E1_SDK_ROOT:-}" ]; then
        candidates+=("${E1_SDK_ROOT}")
    fi
    candidates+=(
        "${PWD}/../noetix_sdk_e1"
        "${PWD}/noetix_sdk_e1"
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

if sdk_root="$(find_sdk_root)"; then
    export E1_SDK_ROOT="${sdk_root}"
    export E1_DDS_CONFIG_PATH="${E1_DDS_CONFIG_PATH:-${E1_SDK_ROOT}/config/dds.xml}"
    if [ -d "${E1_SDK_ROOT}/lib/aarch64" ]; then
        export E1_SDK_LIB_DIR="${E1_SDK_LIB_DIR:-${E1_SDK_ROOT}/lib/aarch64}"
    elif [ -d "${E1_SDK_ROOT}/lib/x86_64" ]; then
        export E1_SDK_LIB_DIR="${E1_SDK_LIB_DIR:-${E1_SDK_ROOT}/lib/x86_64}"
    fi
fi

if [ -n "${E1_SDK_LIB_DIR:-}" ] && [ -d "${E1_SDK_LIB_DIR}" ]; then
    export LD_LIBRARY_PATH="${E1_SDK_LIB_DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

if [ -n "${E1_DDS_CONFIG_PATH:-}" ] && [ -f "${E1_DDS_CONFIG_PATH}" ]; then
    export CYCLONEDDS_URI="${CYCLONEDDS_URI:-file://${E1_DDS_CONFIG_PATH}}"
    if [ "${E1_TRANSPORT}" = "auto" ]; then
        export E1_TRANSPORT="noetix_dds"
    fi
fi

if [ "${E1_TRANSPORT}" = "ros2" ] || [ "${E1_TRANSPORT}" = "auto" ]; then
    for setup in /opt/ros/jazzy/setup.bash /opt/ros/humble/setup.bash; do
        if [ -f "${setup}" ]; then
            # shellcheck disable=SC1090
            source "${setup}"
            export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
            echo "ROS 2: sourced ${setup} (DOMAIN=${ROS_DOMAIN_ID})"
            break
        fi
    done
fi

echo "================================================="
echo "  E1 Server"
echo "  PORT      = ${E1_SERVER_PORT}"
echo "  TRANSPORT = ${E1_TRANSPORT}"
echo "  IFACE     = ${E1_NETWORK_IFACE}"
echo "  SDK_ROOT  = ${E1_SDK_ROOT:-not-found}"
echo "  DDS_XML   = ${E1_DDS_CONFIG_PATH:-not-set}"
echo "================================================="

exec python3 -m bridge.e1_server
