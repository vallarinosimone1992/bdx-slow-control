#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
bdx-generate-displays --config-dir config/profiles/prototype --output-dir phoebus/displays

echo
echo "Environment created at $ROOT_DIR/.venv"
echo "Activate it with: source .venv/bin/activate"
