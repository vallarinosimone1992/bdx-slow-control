#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bdx_stack_shutdown_common.sh
source "$SCRIPT_DIR/bdx_stack_shutdown_common.sh"

usage() {
    cat <<'EOF'
Usage: kill_slow_control_phoebus.sh [--timeout SECONDS] [--force]

Gracefully stop the Phoebus instance recorded by the unified stack launcher.
For macOS application-bundle launches this quits the active Phoebus application
instance with osascript. SIGKILL is used only for direct launches when --force
is supplied.
EOF
}

if ! bdx_shutdown_parse_common_args "$@"; then
    usage
    exit 0
fi

pid_file="$BDX_STACK_RUNTIME_DIR/phoebus.pid"
mode_file="$BDX_STACK_RUNTIME_DIR/phoebus.mode"
mode=""
if [[ -f "$mode_file" ]]; then
    mode="$(<"$mode_file")"
fi

if [[ "$mode" == "macos-app" ]]; then
    if ! command -v osascript >/dev/null 2>&1; then
        bdx_shutdown_die "osascript is required to quit the macOS Phoebus application."
    fi
    if ! osascript -e 'tell application "System Events" to exists process "Phoebus"' \
        | grep -q true; then
        echo "Phoebus is already stopped."
        rm -f "$mode_file" "$pid_file"
        exit 0
    fi
    echo "Quitting the active macOS Phoebus application instance."
    osascript -e 'tell application "Phoebus" to quit'
    rm -f "$mode_file" "$pid_file"
    exit 0
fi

if ! pid="$(bdx_shutdown_read_pid_file "$pid_file")"; then
    rm -f "$pid_file" "$mode_file"
    echo "Phoebus is already stopped."
    exit 0
fi

if ! bdx_shutdown_pid_exists "$pid"; then
    rm -f "$pid_file" "$mode_file"
    echo "Phoebus is already stopped."
    exit 0
fi

command_line="$(bdx_shutdown_command_line "$pid")"
case "$command_line" in
    *Phoebus*|*phoebus*|*org.phoebus*)
        ;;
    *)
        cat >&2 <<EOF
Refusing to stop PID $pid because it does not look like Phoebus.
Recorded command line:
  $command_line
EOF
        exit 2
        ;;
esac

if bdx_shutdown_terminate_pid "$pid" "Phoebus" "$BDX_SHUTDOWN_TIMEOUT" "$BDX_SHUTDOWN_FORCE"; then
    rm -f "$pid_file" "$mode_file"
    echo "Phoebus stopped."
    exit 0
fi

exit 1
