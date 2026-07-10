#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bdx_stack_shutdown_common.sh
source "$SCRIPT_DIR/bdx_stack_shutdown_common.sh"

usage() {
    cat <<'EOF'
Usage: kill_slow_control_ioc.sh [--timeout SECONDS] [--force]

Gracefully stop every BDX prototype IOC process owned by the current user,
including instances not started by the unified stack launcher. SIGKILL is used
only when --force is supplied.
EOF
}

if ! bdx_shutdown_parse_common_args "$@"; then
    usage
    exit 0
fi

pid_file="$BDX_STACK_RUNTIME_DIR/ioc.pid"
pids=()

while IFS= read -r pid; do
    pids+=("$pid")
done < <(
    {
        bdx_shutdown_find_pids_by_all_markers ".venv/bin/bdx-prototype-ioc"
        bdx_shutdown_find_pids_by_all_markers ".local/bin/bdx-prototype-ioc"
        if recorded_pid="$(bdx_shutdown_read_pid_file "$pid_file" 2>/dev/null)"; then
            if bdx_shutdown_pid_exists "$recorded_pid"; then
                command_line="$(bdx_shutdown_command_line "$recorded_pid")"
                if [[ "$command_line" == *"bdx-prototype-ioc"* ]]; then
                    printf "%s\n" "$recorded_pid"
                else
                    echo "Ignoring stale IOC PID file for unrelated process $recorded_pid." >&2
                fi
            fi
        fi
    } | bdx_shutdown_unique_pids
)

if [[ "${#pids[@]}" -eq 0 ]]; then
    rm -f "$pid_file"
    echo "BDX main IOC is already stopped."
    exit 0
fi

if ! bdx_shutdown_terminate_pid_list \
    "BDX main IOC" \
    "$BDX_SHUTDOWN_TIMEOUT" \
    "$BDX_SHUTDOWN_FORCE" \
    "${pids[@]}"; then
    exit 1
fi

remaining=()
while IFS= read -r pid; do
    remaining+=("$pid")
done < <(
    {
        bdx_shutdown_find_pids_by_all_markers ".venv/bin/bdx-prototype-ioc"
        bdx_shutdown_find_pids_by_all_markers ".local/bin/bdx-prototype-ioc"
    } | bdx_shutdown_unique_pids
)

if [[ "${#remaining[@]}" -gt 0 ]]; then
    echo "BDX main IOC processes are still running: ${remaining[*]}" >&2
    exit 1
fi

rm -f "$pid_file"
echo "All BDX main IOC processes stopped."
