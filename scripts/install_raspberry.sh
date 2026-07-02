#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/bdx-slow-control"
CONFIG_DIR="/etc/bdx-slow-control"
SERVICE_TEMPLATE="$ROOT_DIR/systemd/raspberry/bdx-environment-ioc.service.in"
SERVICE_PATH="/etc/systemd/system/bdx-environment-ioc.service"
RASPBERRY_CONFIG="$ROOT_DIR/config/raspberry/environment.json"

usage() {
    cat <<EOF
Usage: sudo $0 [runtime-user]

Installs the BDX MCP9808 environment IOC for Raspberry Pi deployment.
If runtime-user is omitted, SUDO_USER is used when available.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this script with sudo." >&2
    exit 1
fi

runtime_user="${1:-${SUDO_USER:-}}"
if [[ -z "$runtime_user" || "$runtime_user" == "root" ]]; then
    echo "Runtime user is required. Pass it explicitly: sudo $0 <runtime-user>" >&2
    exit 2
fi

if ! id "$runtime_user" >/dev/null 2>&1; then
    echo "Runtime user does not exist: $runtime_user" >&2
    exit 2
fi

runtime_group="$(id -gn "$runtime_user")"
if [[ ! "$runtime_user" =~ ^[A-Za-z0-9._-]+$ || ! "$runtime_group" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "Runtime user and primary group must contain only letters, numbers, dot, underscore, or dash." >&2
    exit 2
fi

if ! command -v rsync >/dev/null 2>&1; then
    echo "rsync is required for installation." >&2
    exit 2
fi

if ! getent group i2c >/dev/null 2>&1; then
    echo "Warning: group 'i2c' does not exist on this host. Enable I2C support before starting the service." >&2
fi

install -d -m 0755 "$APP_DIR"
install -d -m 0755 "$CONFIG_DIR"

rsync -a --delete \
    --exclude .git \
    --exclude .venv \
    --exclude .runtime \
    --exclude __pycache__ \
    --exclude .pytest_cache \
    --exclude .ruff_cache \
    "$ROOT_DIR/" "$APP_DIR/"

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv/bin/python" -m pip install "$APP_DIR"

install -m 0644 "$RASPBERRY_CONFIG" "$CONFIG_DIR/environment.json"

if [[ ! -f "$CONFIG_DIR/bdx.env" ]]; then
    cat > "$CONFIG_DIR/bdx.env" <<'EOF'
# Optional Channel Access server interface override.
# Set this to the Raspberry Pi interface address when the host has multiple interfaces.
# Example:
# BDX_EPICS_INTERFACE=10.0.2.133

BDX_LOG_LEVEL=INFO
EOF
    chmod 0644 "$CONFIG_DIR/bdx.env"
fi

sed \
    -e "s|@BDX_RUNTIME_USER@|$runtime_user|g" \
    -e "s|@BDX_RUNTIME_GROUP@|$runtime_group|g" \
    "$SERVICE_TEMPLATE" > "$SERVICE_PATH"
chmod 0644 "$SERVICE_PATH"

systemctl daemon-reload

cat <<EOF
Raspberry environment IOC installed.

Application directory:
  $APP_DIR

Installed configuration:
  $CONFIG_DIR/environment.json
  $CONFIG_DIR/bdx.env

Installed service:
  $SERVICE_PATH

The service has not been enabled or started.
Review the configuration, set BDX_EPICS_INTERFACE in $CONFIG_DIR/bdx.env if needed, then run:

  sudo systemctl enable bdx-environment-ioc
  sudo systemctl start bdx-environment-ioc
  sudo systemctl status bdx-environment-ioc
EOF
