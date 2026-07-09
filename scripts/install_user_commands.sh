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

commands=(
    bdx_slow_control_start
    bdx_slow_control_kill_ioc
    bdx_slow_control_kill_archiver
    bdx_slow_control_kill_phoebus
    start-bdx-raspberry-ioc
)

for command in "${commands[@]}"; do
    ln -sfn "$VENV_BIN/$command" "$USER_BIN/$command"
done

# Remove obsolete Screen-based commands from earlier installations.
rm -f \
    "$USER_BIN/launch-bdx-slow-control" \
    "$USER_BIN/launch-bdx-phoebus" \
    "$VENV_BIN/launch-bdx-slow-control" \
    "$VENV_BIN/launch-bdx-phoebus"

echo "Installed user commands:"
for command in "${commands[@]}"; do
    echo "  $USER_BIN/$command"
done

case ":$PATH:" in
    *":$USER_BIN:"*)
        ;;
    *)
        echo
        echo "$USER_BIN is not currently in PATH. Add this line to your shell profile:"
        echo "  export PATH=\"$USER_BIN:\$PATH\""
        ;;
esac
