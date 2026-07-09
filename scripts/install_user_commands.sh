#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
VENV_BIN="$ROOT_DIR/.venv/bin"
USER_BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "Repository virtual environment not found: $ROOT_DIR/.venv" >&2
    echo "Run ./scripts/bootstrap.sh first." >&2
    exit 2
fi

"$VENV_PYTHON" -m pip install -e "$ROOT_DIR"

mkdir -p "$USER_BIN"
ln -sfn "$VENV_BIN/launch-bdx-slow-control" "$USER_BIN/launch-bdx-slow-control"
ln -sfn "$VENV_BIN/launch-bdx-phoebus" "$USER_BIN/launch-bdx-phoebus"
ln -sfn "$VENV_BIN/start-bdx-raspberry-ioc" "$USER_BIN/start-bdx-raspberry-ioc"

echo "Installed user commands:"
echo "  $USER_BIN/launch-bdx-slow-control"
echo "  $USER_BIN/launch-bdx-phoebus"
echo "  $USER_BIN/start-bdx-raspberry-ioc"

case ":$PATH:" in
    *":$USER_BIN:"*)
        ;;
    *)
        echo
        echo "$USER_BIN is not currently in PATH. Add this line to your shell profile:"
        echo "  export PATH=\"$USER_BIN:\$PATH\""
        ;;
esac
