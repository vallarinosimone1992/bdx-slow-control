#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this script with sudo." >&2
    exit 1
fi

install -d /opt/bdx-slow-control
install -d /etc/bdx-slow-control

rsync -a --delete \
    --exclude .git \
    --exclude .venv \
    "$ROOT_DIR/" /opt/bdx-slow-control/

python3 -m venv /opt/bdx-slow-control/.venv
/opt/bdx-slow-control/.venv/bin/python -m pip install --upgrade pip
/opt/bdx-slow-control/.venv/bin/python -m pip install /opt/bdx-slow-control

install -m 0644 "$ROOT_DIR"/config/*.json /etc/bdx-slow-control/
if [[ ! -f /etc/bdx-slow-control/bdx.env ]]; then
    install -m 0644 "$ROOT_DIR/.env.example" /etc/bdx-slow-control/bdx.env
fi

install -m 0644 "$ROOT_DIR"/systemd/bdx-prototype-ioc.service /etc/systemd/system/
systemctl daemon-reload

echo "Services installed. Review /etc/bdx-slow-control before enabling them."
