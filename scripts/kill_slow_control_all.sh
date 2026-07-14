#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORCE=0
TIMEOUT_ARGS=()

usage() {
    cat <<'EOF'
Usage: kill_slow_control_all.sh [--timeout SECONDS] [--force]

Gracefully stop normal BDX slow control in this order: Phoebus, IOC.

The independently managed Archiver Appliance is inspected by neither step and
is never stopped, restarted, repaired, or otherwise modified.

The command stops software processes only. It never writes EPICS PVs and never
changes PSU, chiller, network, or Raspberry clock state.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)
            FORCE=1
            shift
            ;;
        --timeout)
            [[ $# -ge 2 ]] || {
                echo "--timeout requires a value." >&2
                exit 2
            }
            TIMEOUT_ARGS=(--timeout "$2")
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

overall=0

run_component() {
    local name="$1"
    shift
    echo "Stopping $name..."
    if "$@"; then
        echo "$name: stopped or already stopped."
    else
        echo "$name: shutdown failed." >&2
        overall=1
    fi
}

force_args=()
if [[ "$FORCE" -eq 1 ]]; then
    force_args=(--force)
fi

run_component "Phoebus" \
    "$SCRIPT_DIR/kill_slow_control_phoebus.sh" \
    ${TIMEOUT_ARGS[@]+"${TIMEOUT_ARGS[@]}"} \
    ${force_args[@]+"${force_args[@]}"}
run_component "BDX main IOC" \
    "$SCRIPT_DIR/kill_slow_control_ioc.sh" \
    ${TIMEOUT_ARGS[@]+"${TIMEOUT_ARGS[@]}"} \
    ${force_args[@]+"${force_args[@]}"}

exit "$overall"
