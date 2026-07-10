#!/usr/bin/env bash

bdx_shutdown_repo_root() {
    cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

BDX_SHUTDOWN_ROOT_DIR="${BDX_SHUTDOWN_ROOT_DIR:-$(bdx_shutdown_repo_root)}"
BDX_STACK_RUNTIME_DIR="${BDX_STACK_RUNTIME_DIR:-$BDX_SHUTDOWN_ROOT_DIR/.runtime/bdx-stack}"
BDX_SHUTDOWN_TIMEOUT="${BDX_SHUTDOWN_TIMEOUT:-10}"
BDX_SHUTDOWN_FORCE=0

bdx_shutdown_die() {
    echo "$*" >&2
    exit 1
}

bdx_shutdown_parse_common_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --force)
                BDX_SHUTDOWN_FORCE=1
                shift
                ;;
            --timeout)
                [[ $# -ge 2 ]] || bdx_shutdown_die "--timeout requires a value."
                BDX_SHUTDOWN_TIMEOUT="$2"
                shift 2
                ;;
            -h|--help)
                return 1
                ;;
            *)
                bdx_shutdown_die "Unknown option: $1"
                ;;
        esac
    done

    if [[ ! "$BDX_SHUTDOWN_TIMEOUT" =~ ^[0-9]+$ ]]; then
        bdx_shutdown_die "Timeout must be a non-negative integer."
    fi
    return 0
}

bdx_shutdown_read_pid_file() {
    local pid_file="$1"
    [[ -f "$pid_file" ]] || return 1
    local pid
    pid="$(<"$pid_file")"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    printf "%s\n" "$pid"
}

bdx_shutdown_pid_exists() {
    local pid="$1"
    local state
    kill -0 "$pid" >/dev/null 2>&1 || return 1
    state="$(ps -p "$pid" -o stat= 2>/dev/null || true)"
    [[ "$state" == Z* ]] && return 1
    return 0
}

bdx_shutdown_command_line() {
    local pid="$1"
    ps -p "$pid" -o command= 2>/dev/null || true
}

bdx_shutdown_process_listing() {
    local uid
    uid="$(id -u)"
    ps -U "$uid" -o pid=,command= 2>/dev/null \
        || ps -u "$uid" -o pid=,command= 2>/dev/null \
        || true
}

bdx_shutdown_find_pids_by_all_markers() {
    local listing pid command marker matched
    listing="$(bdx_shutdown_process_listing)"
    while read -r pid command; do
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        [[ "$pid" -ne "$$" && "$pid" -ne "$PPID" ]] || continue
        matched=1
        for marker in "$@"; do
            if [[ "$command" != *"$marker"* ]]; then
                matched=0
                break
            fi
        done
        if [[ "$matched" -eq 1 ]]; then
            printf "%s\n" "$pid"
        fi
    done <<<"$listing"
}

bdx_shutdown_pid_matches_all_markers() {
    local pid="$1"
    shift
    local command marker
    command="$(bdx_shutdown_command_line "$pid")"
    [[ -n "$command" ]] || return 1
    for marker in "$@"; do
        [[ "$command" == *"$marker"* ]] || return 1
    done
    return 0
}

bdx_shutdown_unique_pids() {
    awk '/^[0-9]+$/ && !seen[$1]++ { print $1 }'
}

bdx_shutdown_wait_for_exit() {
    local pid="$1"
    local timeout="$2"
    local deadline
    deadline=$((SECONDS + timeout))

    while bdx_shutdown_pid_exists "$pid"; do
        if (( SECONDS >= deadline )); then
            return 1
        fi
        sleep 1
    done
    return 0
}

bdx_shutdown_terminate_pid() {
    local pid="$1"
    local label="$2"
    local timeout="$3"
    local force="$4"

    echo "Stopping $label with SIGTERM: $pid"
    kill -TERM "$pid" >/dev/null 2>&1 || return 0
    if bdx_shutdown_wait_for_exit "$pid" "$timeout"; then
        return 0
    fi

    if [[ "$force" -eq 1 ]]; then
        echo "$label did not stop within ${timeout} s; sending SIGKILL because --force was supplied."
        kill -KILL "$pid" >/dev/null 2>&1 || true
        bdx_shutdown_wait_for_exit "$pid" 5 || return 1
        return 0
    fi

    echo "$label did not stop within ${timeout} s. Re-run with --force to use SIGKILL." >&2
    return 1
}

bdx_shutdown_terminate_pid_list() {
    local label="$1"
    local timeout="$2"
    local force="$3"
    shift 3
    local pid failed=0

    for pid in "$@"; do
        bdx_shutdown_pid_exists "$pid" || continue
        if ! bdx_shutdown_terminate_pid "$pid" "$label" "$timeout" "$force"; then
            failed=1
        fi
    done
    return "$failed"
}
