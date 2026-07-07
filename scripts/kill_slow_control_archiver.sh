#!/usr/bin/env bash
set -euo pipefail

ARCHIVER_APP_DIR="${BDX_ARCHIVER_APP_DIR:-$HOME/.local/share/bdx-archiver/app}"
ARCHIVER_ENV_FILE="${BDX_ARCHIVER_ENV_FILE:-$HOME/.config/bdx-archiver/archappl.env}"
ARCHIVER_STATUS="${BDX_ARCHIVER_STATUS:-$ARCHIVER_APP_DIR/scripts/status.sh}"
ARCHIVER_STOP="${BDX_ARCHIVER_STOP:-$ARCHIVER_APP_DIR/scripts/stop.sh}"
ARCHIVER_USER_LOCAL="${BDX_ARCHIVER_USER_LOCAL:-true}"

usage() {
    cat <<'EOF'
Usage: kill_slow_control_archiver.sh

Gracefully stop the BDX Archiver Appliance through its deployment stop script.
This script never sends generic signals to Java processes.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
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

die() {
    echo "$*" >&2
    exit 1
}

archiver_args=(--env "$ARCHIVER_ENV_FILE")
if [[ "$ARCHIVER_USER_LOCAL" == "true" || "$ARCHIVER_USER_LOCAL" == "1" ]]; then
    archiver_args+=(--user-local)
fi

[[ -x "$ARCHIVER_STATUS" ]] || die "Archiver status script not found or not executable: $ARCHIVER_STATUS"
[[ -x "$ARCHIVER_STOP" ]] || die "Archiver stop script not found or not executable: $ARCHIVER_STOP"
[[ -f "$ARCHIVER_ENV_FILE" ]] || die "Archiver environment file not found: $ARCHIVER_ENV_FILE"

archiver_running_count() {
    local output="$1"
    printf "%s\n" "$output" | grep -Ec '^[a-z]+: running pid [0-9]+' || true
}

status_output="$("$ARCHIVER_STATUS" "${archiver_args[@]}" 2>&1 || true)"
running_count="$(archiver_running_count "$status_output")"

if [[ "$running_count" -eq 0 ]]; then
    echo "Archiver Appliance is already stopped."
    exit 0
fi

echo "Stopping Archiver Appliance through: $ARCHIVER_STOP"
"$ARCHIVER_STOP" "${archiver_args[@]}"

status_output="$("$ARCHIVER_STATUS" "${archiver_args[@]}" 2>&1 || true)"
running_count="$(archiver_running_count "$status_output")"
if [[ "$running_count" -eq 0 ]]; then
    echo "Archiver Appliance stopped."
    exit 0
fi

echo "Archiver Appliance did not stop cleanly:" >&2
printf "%s\n" "$status_output" >&2
exit 1
