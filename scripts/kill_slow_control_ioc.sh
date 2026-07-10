#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bdx_stack_shutdown_common.sh
source "$SCRIPT_DIR/bdx_stack_shutdown_common.sh"

usage() {
    cat <<'EOF'
Usage: kill_slow_control_ioc.sh [--timeout SECONDS] [--force]

Gracefully stop every main slow-control IOC process owned by the current user,
including instances that were not started by the unified launcher. Discovery is
restricted to the exact bdx-prototype-ioc command signature. SIGKILL is used
only when --force is supplied.
EOF
}

if ! bdx_shutdown_parse_common_args "$@"; then
    usage
    exit 0
fi

pid_file="$BDX_STACK_RUNTIME_DIR/ioc.pid"
pids=()

append_pid() {
    local candidate="$1"
    local existing
    [[ "$candidate" =~ ^[0-9]+$ ]] || return 0
    for existing in "${pids[@]-}"; do
        [[ "$existing" == "$candidate" ]] && return 0
    done
    pids+=("$candidate")
}

is_slow_control_ioc_command() {
    local command_line="$1"
    [[ "$command_line" == *"bdx-prototype-ioc"* ]]
}

if recorded_pid="$(bdx_shutdown_read_pid_file "$pid_file" 2>/dev/null)"; then
    if bdx_shutdown_pid_exists "$recorded_pid"; then
        recorded_command="$(bdx_shutdown_command_line "$recorded_pid")"
        if is_slow_control_ioc_command "$recorded_command"; then
            append_pid "$recorded_pid"
        else
            echo "Ignoring stale IOC PID file for unrelated PID $recorded_pid." >&2
        fi
    fi
fi

while read -r candidate_pid command_line; do
    [[ -n "${candidate_pid:-}" ]] || continue
    [[ "$candidate_pid" == "$$" ]] && continue
    if is_slow_control_ioc_command "$command_line"; then
        append_pid "$candidate_pid"
    fi
done < <(ps -u "$(id -u)" -o pid=,args=)

if [[ "${#pids[@]}" -eq 0 ]]; then
    rm -f "$pid_file"
    echo "BDX main IOC is already stopped."
    exit 0
fi

overall=0
stopped=0
for pid in "${pids[@]}"; do
    if ! bdx_shutdown_pid_exists "$pid"; then
        continue
    fi
    command_line="$(bdx_shutdown_command_line "$pid")"
    if ! is_slow_control_ioc_command "$command_line"; then
        echo "Refusing to stop PID $pid because its command changed during discovery:" >&2
        echo "  $command_line" >&2
        overall=1
        continue
    fi
    if bdx_shutdown_terminate_pid \
        "$pid" \
        "BDX main IOC" \
        "$BDX_SHUTDOWN_TIMEOUT" \
        "$BDX_SHUTDOWN_FORCE"; then
        stopped=$((stopped + 1))
    else
        overall=1
    fi
done

rm -f "$pid_file"

if [[ "$overall" -eq 0 ]]; then
    echo "Stopped $stopped BDX main IOC process(es)."
fi
exit "$overall"
