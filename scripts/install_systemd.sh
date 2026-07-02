#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/bdx-slow-control"
CONFIG_DIR="/etc/bdx-slow-control"
PROFILE_ROOT="$ROOT_DIR/config/profiles"

usage() {
    cat <<EOF
Usage: sudo $0 <profile> [runtime-user]

Supported profiles:
  main-server  Main BDX server without the environment IOC
  prototype    Full simulated prototype IOC

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

profile="${1:-}"
runtime_user="${2:-${SUDO_USER:-}}"
if [[ -z "$profile" ]]; then
    usage >&2
    exit 2
fi

case "$profile" in
    main-server)
        service_template="$ROOT_DIR/systemd/main-server/bdx-main-server-ioc.service.in"
        service_path="/etc/systemd/system/bdx-main-server-ioc.service"
        ;;
    prototype)
        service_template="$ROOT_DIR/systemd/prototype/bdx-prototype-ioc.service.in"
        service_path="/etc/systemd/system/bdx-prototype-ioc.service"
        ;;
    *)
        echo "Unsupported profile: $profile" >&2
        usage >&2
        exit 2
        ;;
esac

if [[ -z "$runtime_user" || "$runtime_user" == "root" ]]; then
    echo "Runtime user is required. Pass it explicitly: sudo $0 $profile <runtime-user>" >&2
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

profile_source="$PROFILE_ROOT/$profile"
profile_dest="$CONFIG_DIR/profiles/$profile"
if [[ ! -d "$profile_source" ]]; then
    echo "Profile directory not found: $profile_source" >&2
    exit 2
fi

if [[ ! -f "$service_template" ]]; then
    echo "Service template not found: $service_template" >&2
    exit 2
fi

if [[ "$profile" == "main-server" && -e "$profile_source/environment.json" ]]; then
    echo "The main-server profile must not contain environment.json." >&2
    exit 2
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

install -d -m 0755 "$profile_dest"
rsync -a --delete "$profile_source/" "$profile_dest/"

if [[ ! -f "$CONFIG_DIR/bdx.env" ]]; then
    install -m 0644 "$ROOT_DIR/.env.example" "$CONFIG_DIR/bdx.env"
fi

sed \
    -e "s|@BDX_RUNTIME_USER@|$runtime_user|g" \
    -e "s|@BDX_RUNTIME_GROUP@|$runtime_group|g" \
    "$service_template" > "$service_path"
chmod 0644 "$service_path"

systemctl daemon-reload

cat <<EOF
BDX profile installed: $profile

Application directory:
  $APP_DIR

Installed configuration profile:
  $profile_dest

Installed service:
  $service_path

The service has not been enabled or started.
Review $CONFIG_DIR/bdx.env and $profile_dest before enabling the service.
EOF
