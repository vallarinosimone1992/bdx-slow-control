#!/usr/bin/env bash
set -euo pipefail

ARCHIVER_APP_DIR="${BDX_ARCHIVER_APP_DIR:-$HOME/.local/share/bdx-archiver/app}"
ARCHIVER_ENV_FILE="${BDX_ARCHIVER_ENV_FILE:-$HOME/.config/bdx-archiver/archappl.env}"
ARCHIVER_STATUS="${BDX_ARCHIVER_STATUS:-$ARCHIVER_APP_DIR/scripts/status.sh}"
ARCHIVER_STOP="${BDX_ARCHIVER_STOP:-$ARCHIVER_APP_DIR/scripts/stop.sh}"
ARCHIVER_USER_LOCAL="${BDX_ARCHIVER_USER_LOCAL:-true}"
ARCHIVER_SERVICE_NAME="${BDX_ARCHIVER_SERVICE_NAME:-bdx-archiver-user.service}"

usage() {
    cat <<'EOF'
Usage: kill_slow_control_archiver.sh

Gracefully stop the BDX Archiver Appliance through its deployment stop script.
This script never sends generic signals to Java processes and never touches an
IOC or Phoebus.
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

if command -v systemctl >/dev/null 2>&1 && \
   [[ "$(systemctl --user show "$ARCHIVER_SERVICE_NAME" -p LoadState --value 2>/dev/null || true)" == "loaded" ]] && \
   ! systemctl --user is-active --quiet "$ARCHIVER_SERVICE_NAME" 2>/dev/null; then
    echo "Archiver user service is already inactive."
elif command -v systemctl >/dev/null 2>&1 && \
     [[ "$(systemctl --user show "$ARCHIVER_SERVICE_NAME" -p LoadState --value 2>/dev/null || true)" == "loaded" ]]; then
    echo "Stopping Archiver user service: $ARCHIVER_SERVICE_NAME"
    systemctl --user stop "$ARCHIVER_SERVICE_NAME"
fi

echo "Reconciling Archiver components through: $ARCHIVER_STOP"
"$ARCHIVER_STOP" "${archiver_args[@]}"

status_output="$("$ARCHIVER_STATUS" "${archiver_args[@]}" 2>&1 || true)"
running_count="$(archiver_running_count "$status_output")"
occupied_ports=()
for port in 17665 17666 17667 17668; do
    if curl -sS --connect-timeout 1 --max-time 1 \
        --output /dev/null "http://127.0.0.1:$port/" >/dev/null 2>&1; then
        occupied_ports+=("$port")
    fi
done

if [[ "$running_count" -eq 0 && "${#occupied_ports[@]}" -eq 0 ]]; then
    echo "Archiver Appliance stopped."
    exit 0
fi

echo "Archiver Appliance did not stop cleanly:" >&2
printf "%s\n" "$status_output" >&2
if [[ "${#occupied_ports[@]}" -gt 0 ]]; then
    echo "Occupied Archiver ports: ${occupied_ports[*]}" >&2
fi
exit 1
