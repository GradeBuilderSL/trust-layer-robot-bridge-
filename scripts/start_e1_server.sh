#!/usr/bin/env bash
# start_e1_server.sh — launch the on-robot REST wrapper for Noetix E1.
#
# Run THIS on E1's Jetson Orin Nano Super (NOT on the RK3588S motion-control
# board — that will starve EtherCAT and crash the robot).
#
# Usage on the Jetson:
#   ssh noetix@192.168.55.101    # password: noetix
#   cd /opt/trust-layer-bridge
#   bash scripts/start_e1_server.sh                # auto-pick transport
#   E1_TRANSPORT=ros2 bash scripts/start_e1_server.sh
#   E1_TRANSPORT=sim  bash scripts/start_e1_server.sh    # no hardware, dev only
#
set -euo pipefail

cd "$(dirname "$0")/.."

export E1_SERVER_PORT="${E1_SERVER_PORT:-8083}"
export E1_TRANSPORT="${E1_TRANSPORT:-auto}"
export E1_NETWORK_IFACE="${E1_NETWORK_IFACE:-eth0}"
export E1_ROBOT_ID="${E1_ROBOT_ID:-e1-01}"
export E1_ROBOT_NAME="${E1_ROBOT_NAME:-Noetix E1}"

# Source ROS 2 if requested
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

echo "═══════════════════════════════════════════════"
echo "  E1 Server"
echo "  PORT      = ${E1_SERVER_PORT}"
echo "  TRANSPORT = ${E1_TRANSPORT}"
echo "  IFACE     = ${E1_NETWORK_IFACE}"
echo "═══════════════════════════════════════════════"

exec python3 -m bridge.e1_server
