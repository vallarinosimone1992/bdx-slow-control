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

ARCHIVER_ENV_FILE="${BDX_ARCHIVER_ENV_FILE:-$HOME/.config/bdx-archiver/archappl.env}"
if [[ -f "$ARCHIVER_ENV_FILE" ]]; then
    "$ROOT_DIR/deploy/archiver-appliance/scripts/install.sh" \
        --env "$ARCHIVER_ENV_FILE" \
        --user-local
else
    echo "Archiver expert environment is not installed; skipping deployment refresh: $ARCHIVER_ENV_FILE"
fi

mkdir -p "$USER_BIN"

canonical_commands=(
    bdx_slow_control_start
    bdx_slow_control_kill
    bdx_slow_control_kill_ioc
    bdx_slow_control_kill_phoebus
    bdx_archiver_start
    bdx_archiver_repair
    bdx_archiver_audit
    bdx_archiver_kill
)

# Temporary compatibility aliases for existing deployments and external
# operator procedures. New deployments should use canonical_commands above.
compatibility_aliases=(
    start_slow_control
    kill_slow_control
    start_archiver
    kill_archiver
    bdx_slow_control_start_archiver
    bdx_slow_control_repair_archiver
    bdx_slow_control_kill_archiver
)

additional_commands=(
    start-bdx-raspberry-ioc
)

commands=(
    "${canonical_commands[@]}"
    "${compatibility_aliases[@]}"
    "${additional_commands[@]}"
)

USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
install -d -m 0755 "$USER_SYSTEMD_DIR"
install -m 0644 \
    "$ROOT_DIR/deploy/archiver-appliance/systemd/bdx-archiver-user.service" \
    "$USER_SYSTEMD_DIR/bdx-archiver-user.service"
systemctl --user daemon-reload

for command in "${commands[@]}"; do
    ln -sfn "$VENV_BIN/$command" "$USER_BIN/$command"
done

# Remove obsolete Screen-based commands from earlier installations.
rm -f \
    "$USER_BIN/launch-bdx-slow-control" \
    "$USER_BIN/launch-bdx-phoebus" \
    "$VENV_BIN/launch-bdx-slow-control" \
    "$VENV_BIN/launch-bdx-phoebus"

echo "Installed canonical user commands:"
for command in "${canonical_commands[@]}"; do
    echo "  $USER_BIN/$command"
done

echo "Installed temporary compatibility aliases:"
for command in "${compatibility_aliases[@]}"; do
    echo "  $USER_BIN/$command"
done

echo "Installed additional user commands:"
for command in "${additional_commands[@]}"; do
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
