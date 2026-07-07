#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bdx_stack_shutdown_common.sh
source "$SCRIPT_DIR/bdx_stack_shutdown_common.sh"

usage() {
    cat <<'EOF'
Usage: kill_slow_control_ioc.sh [--timeout SECONDS] [--force]

Gracefully stop only the BDX main IOC process recorded by the unified stack
launcher. SIGKILL is used only when --force is supplied.
EOF
}

if ! bdx_shutdown_parse_common_args "$@"; then
    usage
    exit 0
fi

pid_file="$BDX_STACK_RUNTIME_DIR/ioc.pid"
if ! pid="$(bdx_shutdown_read_pid_file "$pid_file")"; then
    rm -f "$pid_file"
    echo "BDX main IOC is already stopped."
    exit 0
fi

if ! bdx_shutdown_pid_exists "$pid"; then
    rm -f "$pid_file"
    echo "BDX main IOC is already stopped."
    exit 0
fi

command_line="$(bdx_shutdown_command_line "$pid")"
if [[ "$command_line" != *"bdx-prototype-ioc"* ]]; then
    cat >&2 <<EOF
Refusing to stop PID $pid because it does not look like bdx-prototype-ioc.
Recorded command line:
  $command_line
EOF
    exit 2
fi

if bdx_shutdown_terminate_pid "$pid" "BDX main IOC" "$BDX_SHUTDOWN_TIMEOUT" "$BDX_SHUTDOWN_FORCE"; then
    rm -f "$pid_file"
    echo "BDX main IOC stopped."
    exit 0
fi

exit 1
