#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x .venv/bin/bdx-prototype-ioc ]]; then
    echo "Virtual environment is missing. Run ./scripts/bootstrap.sh first." >&2
    exit 1
fi

ioc_pid=""
cleanup() {
    if [[ -n "$ioc_pid" ]]; then
        kill "$ioc_pid" 2>/dev/null || true
        wait "$ioc_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

if ! .venv/bin/caproto-get BDX:GLOBAL:SYSTEM_STATE >/dev/null 2>&1; then
    echo "Starting the BDX prototype IOC"
    .venv/bin/bdx-prototype-ioc --config-dir config &
    ioc_pid="$!"
    for _ in {1..30}; do
        if .venv/bin/caproto-get BDX:GLOBAL:SYSTEM_STATE >/dev/null 2>&1; then
            break
        fi
        sleep 0.5
    done
else
    echo "Using an already running BDX prototype IOC"
fi

if ! .venv/bin/caproto-get BDX:GLOBAL:SYSTEM_STATE >/dev/null 2>&1; then
    echo "The prototype IOC did not become reachable." >&2
    exit 1
fi

"$ROOT_DIR/scripts/launch_phoebus.sh" "${1:-overview}"
