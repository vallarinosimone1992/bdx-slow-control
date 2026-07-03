#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/bdx-slow-control"
CONFIG_DIR="/etc/bdx-slow-control"
SERVICE_TEMPLATE="$ROOT_DIR/systemd/raspberry/bdx-environment-ioc.service.in"
SERVICE_PATH="/etc/systemd/system/bdx-environment-ioc.service"
RASPBERRY_PROFILE="$ROOT_DIR/config/profiles/raspberry"
RASPBERRY_CONFIG="$RASPBERRY_PROFILE/environment.json"
RASPBERRY_ENV="$RASPBERRY_PROFILE/bdx.env"

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

if [[ ! -f "$RASPBERRY_CONFIG" ]]; then
    echo "Raspberry environment configuration not found: $RASPBERRY_CONFIG" >&2
    exit 2
fi

if [[ ! -f "$RASPBERRY_ENV" ]]; then
    echo "Raspberry IOC environment file not found: $RASPBERRY_ENV" >&2
    exit 2
fi

if [[ ! -f "$SERVICE_TEMPLATE" ]]; then
    echo "Service template not found: $SERVICE_TEMPLATE" >&2
    exit 2
fi

if ! getent group i2c >/dev/null 2>&1; then
    echo "Warning: group 'i2c' does not exist on this host. Enable I2C support before starting the service." >&2
fi

install -d -m 0755 "$APP_DIR"
install -d -m 0755 "$CONFIG_DIR/profiles"

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

install -d -m 0755 "$CONFIG_DIR/profiles/raspberry"
rsync -a --delete --exclude bdx.env "$RASPBERRY_PROFILE/" "$CONFIG_DIR/profiles/raspberry/"

if [[ -f "$CONFIG_DIR/bdx.env" ]] && ! cmp -s "$RASPBERRY_ENV" "$CONFIG_DIR/bdx.env"; then
    backup_path="$CONFIG_DIR/bdx.env.previous.$(date -u +%Y%m%dT%H%M%SZ)"
    cp -p "$CONFIG_DIR/bdx.env" "$backup_path"
    echo "Previous IOC environment file preserved as: $backup_path"
fi
install -m 0644 "$RASPBERRY_ENV" "$CONFIG_DIR/bdx.env"

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
  $CONFIG_DIR/profiles/raspberry/environment.json
  $CONFIG_DIR/bdx.env

Installed service:
  $SERVICE_PATH

The service has not been enabled or started.
The IOC environment is installed from the repository-controlled Raspberry profile.
It binds Channel Access to 172.22.50.10 through BDX_EPICS_INTERFACE.

NetworkManager is not configured by this installer. Configure the dedicated Ethernet
profile explicitly when needed:

  sudo ./scripts/configure_raspberry_network.sh

Then run:

  # Ensure /boot/firmware/config.txt contains:
  # dtoverlay=i2c6,pins_22_23,baudrate=10000
  # Reboot after adding the overlay, then verify with:
  # i2cdetect -l
  # sudo i2cdetect -y 6
  sudo -u $runtime_user $APP_DIR/.venv/bin/bdx-environment-check --config $CONFIG_DIR/profiles/raspberry/environment.json
  sudo systemctl enable bdx-environment-ioc
  sudo systemctl start bdx-environment-ioc
  sudo systemctl status bdx-environment-ioc
EOF
