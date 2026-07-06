#!/usr/bin/env bash
#
# Configure Simone's macOS laptop Ethernet interface for the BDX slow-control LAN.
#
# Usage:
#   sudo ./scripts/simone_laptop_ethernet.sh [interface]
#
# Examples:
#   sudo ./scripts/simone_laptop_ethernet.sh
#   sudo ./scripts/simone_laptop_ethernet.sh en7
#

set -euo pipefail

DEFAULT_INTERFACE="en8"
INTERFACE="${1:-$DEFAULT_INTERFACE}"

IP_ADDRESS="172.22.50.2"
NETMASK="255.255.255.0"
TEST_HOST="172.22.50.20"

usage() {
    cat <<EOF
Usage: sudo $0 [interface]

Configure a macOS Ethernet interface for the BDX slow-control network.

Arguments:
  interface   Network interface to configure (default: ${DEFAULT_INTERFACE})

Configuration:
  address     ${IP_ADDRESS}
  netmask     ${NETMASK}
  network     172.22.50.0/24
EOF
}

if [[ "${INTERFACE}" == "-h" || "${INTERFACE}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "Error: this script is intended for macOS." >&2
    exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
    echo "Error: this script must be run with sudo." >&2
    echo "Usage: sudo $0 [interface]" >&2
    exit 1
fi

if ! ifconfig "${INTERFACE}" >/dev/null 2>&1; then
    echo "Error: network interface '${INTERFACE}' does not exist." >&2
    echo "Available interfaces:" >&2
    ifconfig -l >&2
    exit 1
fi

echo "Configuring ${INTERFACE} for the BDX slow-control network..."
ifconfig "${INTERFACE}" inet "${IP_ADDRESS}" netmask "${NETMASK}" up

CONFIGURED_IP="$(
    ifconfig "${INTERFACE}" |
        awk '/inet / {print $2}' |
        grep -Fx "${IP_ADDRESS}" |
        head -n 1 || true
)"

if [[ "${CONFIGURED_IP}" != "${IP_ADDRESS}" ]]; then
    echo "Error: failed to assign ${IP_ADDRESS} to ${INTERFACE}." >&2
    exit 1
fi

ROUTE_INTERFACE="$(
    route -n get "${TEST_HOST}" 2>/dev/null |
        awk '/interface:/ {print $2; exit}'
)"

if [[ "${ROUTE_INTERFACE}" != "${INTERFACE}" ]]; then
    echo "Error: traffic to ${TEST_HOST} is routed through '${ROUTE_INTERFACE:-unknown}'," >&2
    echo "       not through '${INTERFACE}'." >&2
    exit 1
fi

echo
echo "BDX slow-control Ethernet configuration applied successfully:"
echo "  Interface: ${INTERFACE}"
echo "  Address:   ${IP_ADDRESS}"
echo "  Netmask:   ${NETMASK}"
echo "  Route:     ${TEST_HOST} via ${ROUTE_INTERFACE}"
echo
echo "This configuration is temporary and may need to be reapplied after"
echo "disconnecting the adapter or rebooting the laptop."
