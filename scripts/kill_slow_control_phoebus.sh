#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bdx_stack_shutdown_common.sh
source "$SCRIPT_DIR/bdx_stack_shutdown_common.sh"

usage() {
    cat <<'EOF'
Usage: kill_slow_control_phoebus.sh [--timeout SECONDS] [--force]

Gracefully stop every Phoebus process owned by the current user that is tied to
the BDX slow-control settings or display directory, including instances started
outside the unified launcher. Other Phoebus sessions and BDX DAQ processes are
left untouched. SIGKILL is used only when --force is supplied.
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
    if osascript -e 'tell application "System Events" to exists process "Phoebus"' \
        | grep -q true; then
        echo "Quitting the active macOS Phoebus application instance."
        osascript -e 'tell application "Phoebus" to quit'
    fi
fi

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

is_bdx_slow_control_phoebus_command() {
    local command_line="$1"
    local is_phoebus=0
    local is_slow_control=0

    case "$command_line" in
        *"product-"*".jar"*|*"org.phoebus"*|*"phoebus.sh"*)
            is_phoebus=1
            ;;
    esac
    case "$command_line" in
        *"bdx-phoebus/settings.ini"*|*"bdx-slow-control/phoebus/displays/"*)
            is_slow_control=1
            ;;
    esac

    [[ "$is_phoebus" -eq 1 && "$is_slow_control" -eq 1 ]]
}

if recorded_pid="$(bdx_shutdown_read_pid_file "$pid_file" 2>/dev/null)"; then
    if bdx_shutdown_pid_exists "$recorded_pid"; then
        recorded_command="$(bdx_shutdown_command_line "$recorded_pid")"
        if is_bdx_slow_control_phoebus_command "$recorded_command"; then
            append_pid "$recorded_pid"
        else
            echo "Ignoring stale Phoebus PID file for unrelated PID $recorded_pid." >&2
        fi
    fi
fi

while read -r candidate_pid command_line; do
    [[ -n "${candidate_pid:-}" ]] || continue
    [[ "$candidate_pid" == "$$" ]] && continue
    if is_bdx_slow_control_phoebus_command "$command_line"; then
        append_pid "$candidate_pid"
    fi
done < <(ps -u "$(id -u)" -o pid=,args=)

if [[ "${#pids[@]}" -eq 0 ]]; then
    rm -f "$pid_file" "$mode_file"
    echo "BDX slow-control Phoebus is already stopped."
    exit 0
fi

overall=0
stopped=0
for pid in "${pids[@]}"; do
    if ! bdx_shutdown_pid_exists "$pid"; then
        continue
    fi
    command_line="$(bdx_shutdown_command_line "$pid")"
    if ! is_bdx_slow_control_phoebus_command "$command_line"; then
        echo "Refusing to stop PID $pid because its command changed during discovery:" >&2
        echo "  $command_line" >&2
        overall=1
        continue
    fi
    if bdx_shutdown_terminate_pid \
        "$pid" \
        "BDX slow-control Phoebus" \
        "$BDX_SHUTDOWN_TIMEOUT" \
        "$BDX_SHUTDOWN_FORCE"; then
        stopped=$((stopped + 1))
    else
        overall=1
    fi
done

rm -f "$pid_file" "$mode_file"

if [[ "$overall" -eq 0 ]]; then
    echo "Stopped $stopped BDX slow-control Phoebus process(es)."
fi
exit "$overall"
