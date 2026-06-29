#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x .venv/bin/bdx-prototype-ioc ]]; then
    echo "Virtual environment is missing. Run ./scripts/bootstrap.sh first." >&2
    exit 1
fi

exec .venv/bin/bdx-prototype-ioc --config-dir config
