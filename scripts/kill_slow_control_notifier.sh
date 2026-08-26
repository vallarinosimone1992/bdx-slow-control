#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bdx_stack_shutdown_common.sh
source "$SCRIPT_DIR/bdx_stack_shutdown_common.sh"

usage() {
    cat <<'EOF'
Usage: kill_slow_control_notifier.sh [--timeout SECONDS] [--force]

Gracefully stop every BDX notifier process owned by the current user. Process
discovery uses the dedicated bdx-slow-control-notifier command marker, so the
notifier installation path may differ between development and deployment.
EOF
}

if ! bdx_shutdown_parse_common_args "$@"; then
    usage
    exit 0
fi

pid_file="$BDX_STACK_RUNTIME_DIR/notifier.pid"
pids=()
invalid_recorded_pid=0

append_pid() {
    local candidate="$1"
    local existing
    [[ "$candidate" =~ ^[0-9]+$ ]] || return 0
    for existing in "${pids[@]-}"; do
        [[ "$existing" == "$candidate" ]] && return 0
    done
    pids+=("$candidate")
}

is_notifier_command() {
    local command_line="$1"
    [[ "$command_line" == *"--service-instance bdx-slow-control-notifier"* ]]
}

if recorded_pid="$(bdx_shutdown_read_pid_file "$pid_file" 2>/dev/null)"; then
    if bdx_shutdown_pid_exists "$recorded_pid"; then
        recorded_command="$(bdx_shutdown_command_line "$recorded_pid")"
        if is_notifier_command "$recorded_command"; then
            append_pid "$recorded_pid"
        else
            cat >&2 <<EOF
Refusing to stop PID $recorded_pid because it is not the BDX notifier.
Recorded command line:
  $recorded_command
EOF
            invalid_recorded_pid=1
        fi
    else
        rm -f "$pid_file"
    fi
fi

while read -r candidate_pid command_line; do
    [[ -n "${candidate_pid:-}" ]] || continue
    [[ "$candidate_pid" == "$$" ]] && continue
    if is_notifier_command "$command_line"; then
        append_pid "$candidate_pid"
    fi
done < <(ps -u "$(id -u)" -o pid=,args=)

if [[ "${#pids[@]}" -eq 0 ]]; then
    if [[ "$invalid_recorded_pid" -eq 1 ]]; then
        exit 2
    fi
    rm -f "$pid_file"
    echo "BDX notifier is already stopped."
    exit 0
fi

overall=0
if [[ "$invalid_recorded_pid" -eq 1 ]]; then
    overall=2
fi
stopped=0
for pid in "${pids[@]}"; do
    if ! bdx_shutdown_pid_exists "$pid"; then
        continue
    fi
    command_line="$(bdx_shutdown_command_line "$pid")"
    if ! is_notifier_command "$command_line"; then
        echo "Refusing to stop PID $pid because its command changed during discovery:" >&2
        echo "  $command_line" >&2
        [[ "$overall" -ne 2 ]] && overall=1
        continue
    fi
    if bdx_shutdown_terminate_pid \
        "$pid" \
        "BDX notifier" \
        "$BDX_SHUTDOWN_TIMEOUT" \
        "$BDX_SHUTDOWN_FORCE"; then
        stopped=$((stopped + 1))
    else
        [[ "$overall" -ne 2 ]] && overall=1
    fi
done

if [[ "$invalid_recorded_pid" -eq 0 ]]; then
    rm -f "$pid_file"
fi

echo "Stopped $stopped BDX notifier process(es)."
exit "$overall"
