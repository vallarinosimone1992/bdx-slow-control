#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${1:-$ROOT_DIR/config/deployment/raspberry-network.env}"

fail() {
    local message="$1"
    local code="${2:-2}"
    echo "$message" >&2
    exit "$code"
}

validate_required_value() {
    local name="$1"
    local value="${!name:-}"
    if [[ -z "$value" ]]; then
        fail "$name is required."
    fi
    if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
        fail "$name must not contain newlines."
    fi
}

validate_ipv4_cidr() {
    local cidr="$1"
    local ip
    local octet
    local -a octets

    if [[ ! "$cidr" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$ ]]; then
        return 1
    fi

    ip="${cidr%/*}"
    IFS=. read -r -a octets <<< "$ip"
    for octet in "${octets[@]}"; do
        if (( 10#$octet > 255 )); then
            return 1
        fi
    done
}

if [[ "${EUID}" -ne 0 ]]; then
    fail "Run this script with sudo." 1
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
    fail "Network configuration not found: $CONFIG_FILE"
fi

# shellcheck source=/dev/null
source "$CONFIG_FILE"

validate_required_value BDX_ETHERNET_INTERFACE
validate_required_value BDX_ETHERNET_CONNECTION
validate_required_value BDX_ETHERNET_ADDRESS

interface="$BDX_ETHERNET_INTERFACE"
connection="$BDX_ETHERNET_CONNECTION"
address="$BDX_ETHERNET_ADDRESS"

if [[ ! "$interface" =~ ^[A-Za-z0-9_.:-]+$ ]]; then
    fail "Invalid Ethernet interface name: $interface"
fi

if [[ "$connection" == -* ]]; then
    fail "NetworkManager connection name must not start with '-'."
fi

if ! validate_ipv4_cidr "$address"; then
    fail "Invalid IPv4 CIDR address: $address"
fi

if ! command -v nmcli >/dev/null 2>&1; then
    fail "nmcli is required."
fi

if ! systemctl is-active --quiet NetworkManager; then
    fail "NetworkManager is not active."
fi

if [[ ! -d "/sys/class/net/$interface" ]]; then
    fail "Network interface does not exist: $interface"
fi

if nmcli -g NAME connection show | grep -Fxq "$connection"; then
    existing_type="$(nmcli -g connection.type connection show "$connection")"
    if [[ "$existing_type" != "802-3-ethernet" && "$existing_type" != "ethernet" ]]; then
        fail "Existing NetworkManager connection is not Ethernet: $connection"
    fi
    echo "Updating NetworkManager connection: $connection"
else
    echo "Creating NetworkManager connection: $connection"
    nmcli connection add \
        type ethernet \
        ifname "$interface" \
        con-name "$connection"
fi

nmcli connection modify "$connection" \
    connection.interface-name "$interface" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100 \
    ipv4.method manual \
    ipv4.addresses "$address" \
    ipv4.gateway "" \
    ipv4.dns "" \
    ipv4.routes "" \
    ipv4.never-default yes \
    ipv4.ignore-auto-dns yes \
    ipv6.never-default yes

while IFS= read -r other_connection; do
    [[ -n "$other_connection" ]] || continue
    [[ "$other_connection" != "$connection" ]] || continue

    other_type="$(
        nmcli -g connection.type connection show "$other_connection" 2>/dev/null ||
        true
    )"
    other_interface="$(
        nmcli -g connection.interface-name connection show "$other_connection" \
            2>/dev/null || true
    )"

    if [[ ( "$other_type" == "802-3-ethernet" || "$other_type" == "ethernet" ) &&
          "$other_interface" == "$interface" ]]; then
        echo "Disabling autoconnect for old Ethernet profile: $other_connection"
        nmcli connection modify "$other_connection" connection.autoconnect no
    fi
done < <(nmcli -g NAME connection show)

nmcli connection up "$connection"

echo
echo "Configured interface:"
ip -br address show "$interface"

echo
echo "IPv4 routes:"
ip -4 route
