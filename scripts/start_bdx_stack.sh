#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_ENV_FILE="${BDX_RUNTIME_ENV:-$ROOT_DIR/config/runtime.env}"
ARCHIVER_SCRIPT_DIR="$ROOT_DIR/deploy/archiver-appliance/scripts"
BDX_STACK_RUNTIME_DIR="${BDX_STACK_RUNTIME_DIR:-$ROOT_DIR/.runtime/bdx-stack}"
IOC_PID_FILE="$BDX_STACK_RUNTIME_DIR/ioc.pid"

# shellcheck source=../deploy/archiver-appliance/scripts/common.sh
source "$ARCHIVER_SCRIPT_DIR/common.sh"

RASPBERRY_EPICS_HOST="172.22.50.10"
LV1_ENDPOINT="172.22.50.20:9221"
LV2_ENDPOINT="172.22.50.21:9221"
CHILLER_ENDPOINT="172.22.50.60:54321"

VENV_DIR="$ROOT_DIR/.venv"
IOC_COMMAND="$VENV_DIR/bin/bdx-prototype-ioc"
CAPROTO_GET="$VENV_DIR/bin/caproto-get"
PHOEBUS_LAUNCHER="$ROOT_DIR/scripts/launch_phoebus.sh"

ARCHIVER_APP_DIR="$HOME/.local/share/bdx-archiver/app"
ARCHIVER_ENV_FILE="$HOME/.config/bdx-archiver/archappl.env"
ARCHIVER_HEALTHCHECK="$ARCHIVER_SCRIPT_DIR/healthcheck.sh"
ARCHIVER_START="$ARCHIVER_SCRIPT_DIR/start.sh"
ARCHIVER_STATUS="$ARCHIVER_SCRIPT_DIR/status.sh"
ARCHIVER_AUTOREGISTER="$ARCHIVER_SCRIPT_DIR/auto-register-pvs.sh"
ARCHIVER_MGMT_URL="http://127.0.0.1:17665/mgmt/bpl"
ARCHIVER_ENGINE_URL="http://127.0.0.1:17666/engine/bpl"
ARCHIVER_ETL_URL="http://127.0.0.1:17667/etl/bpl"
ARCHIVER_RETRIEVAL_BPL_URL="http://127.0.0.1:17668/retrieval/bpl"
ARCHIVER_RETRIEVAL_URL="http://127.0.0.1:17668/retrieval"

IOC_READY_PV="BDX:PSU:LV1:CH1:VOLTAGE_RBV"
ARCHIVER_READY_PV="BDX:PSU:LV1:CH1:VOLTAGE_RBV"
PHOEBUS_PREFLIGHT_PV="BDX:ENV:TEMP:T00:VALUE"
BDX_MAIN_HOST_SOURCE=""
BDX_MAIN_HOST_CLI=""
BDX_MAIN_HOST_CLI_SET=0
BDX_STACK_ALLOW_LOOPBACK=0

bdx_stack_die() {
    echo "$*" >&2
    exit 1
}

bdx_stack_usage() {
    cat <<'EOF'
Usage: start_bdx_stack.sh [options] [display]

Options:
  --main-host ADDRESS   Main IOC slow-control LAN address.
  --allow-loopback      Permit 127.0.0.1 as the main IOC address.
  -h, --help            Show this help.

The main IOC address is otherwise read from BDX_MAIN_HOST in the environment
or config/runtime.env. Operational use must not silently fall back to loopback.
EOF
}

bdx_stack_parse_args() {
    BDX_STACK_DISPLAY="overview"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --main-host)
                [[ $# -ge 2 ]] || bdx_stack_die "--main-host requires an address."
                BDX_MAIN_HOST_CLI="$2"
                BDX_MAIN_HOST_CLI_SET=1
                shift 2
                ;;
            --allow-loopback)
                BDX_STACK_ALLOW_LOOPBACK=1
                shift
                ;;
            -h|--help)
                bdx_stack_usage
                exit 0
                ;;
            --)
                shift
                break
                ;;
            -*)
                bdx_stack_die "Unknown option: $1"
                ;;
            *)
                BDX_STACK_DISPLAY="$1"
                shift
                break
                ;;
        esac
    done

    if [[ $# -gt 0 ]]; then
        bdx_stack_die "Unexpected extra argument: $1"
    fi
}

bdx_stack_read_runtime_host() {
    local saved_host="${BDX_MAIN_HOST-}"
    local had_host=0
    if [[ "${BDX_MAIN_HOST+x}" == x ]]; then
        had_host=1
    fi

    unset BDX_MAIN_HOST
    # shellcheck disable=SC1090
    source "$RUNTIME_ENV_FILE"
    printf "%s\n" "${BDX_MAIN_HOST-}"

    if [[ "$had_host" -eq 1 ]]; then
        BDX_MAIN_HOST="$saved_host"
    else
        unset BDX_MAIN_HOST
    fi
}

bdx_stack_validate_ipv4_address() {
    local value="$1"
    local part
    local -a parts
    IFS=. read -r -a parts <<<"$value"
    [[ "${#parts[@]}" -eq 4 ]] || return 1
    for part in "${parts[@]}"; do
        [[ "$part" =~ ^[0-9]+$ ]] || return 1
        (( 10#$part >= 0 && 10#$part <= 255 )) || return 1
    done
    return 0
}

bdx_stack_validate_main_host() {
    if [[ -z "$BDX_MAIN_HOST" ]]; then
        cat >&2 <<EOF
BDX_MAIN_HOST is required.

Create config/runtime.env or pass --main-host with the IP address of the
computer running the main IOC, for example:

  BDX_MAIN_HOST=172.22.50.2
EOF
        exit 2
    fi
    if ! bdx_stack_validate_ipv4_address "$BDX_MAIN_HOST"; then
        bdx_stack_die "BDX_MAIN_HOST must be a valid IPv4 address: $BDX_MAIN_HOST"
    fi
    if [[ "$BDX_MAIN_HOST" == "0.0.0.0" ]]; then
        bdx_stack_die "BDX_MAIN_HOST must not be 0.0.0.0."
    fi
    if [[ "$BDX_MAIN_HOST" == "127.0.0.1" && "$BDX_STACK_ALLOW_LOOPBACK" -ne 1 ]]; then
        cat >&2 <<'EOF'
BDX_MAIN_HOST=127.0.0.1 is not valid for operational use because remote
Channel Access clients cannot reach a loopback-only IOC.

Use the main host slow-control LAN address, for example:

  BDX_MAIN_HOST=172.22.50.2

For local-only development, rerun with --allow-loopback.
EOF
        exit 2
    fi
}

bdx_stack_load_runtime_environment() {
    local environment_host="${BDX_MAIN_HOST-}"
    local runtime_host=""

    if [[ -f "$RUNTIME_ENV_FILE" ]]; then
        runtime_host="$(bdx_stack_read_runtime_host)"
    fi

    if [[ "$BDX_MAIN_HOST_CLI_SET" -eq 1 ]]; then
        BDX_MAIN_HOST="$BDX_MAIN_HOST_CLI"
        BDX_MAIN_HOST_SOURCE="command-line option"
    elif [[ -n "$environment_host" ]]; then
        BDX_MAIN_HOST="$environment_host"
        BDX_MAIN_HOST_SOURCE="environment"
    elif [[ -n "$runtime_host" ]]; then
        BDX_MAIN_HOST="$runtime_host"
        BDX_MAIN_HOST_SOURCE="config/runtime.env"
    else
        BDX_MAIN_HOST=""
        BDX_MAIN_HOST_SOURCE="unset"
    fi

    bdx_stack_validate_main_host
    export BDX_MAIN_HOST
    export BDX_EPICS_INTERFACE="$BDX_MAIN_HOST"
    export EPICS_CA_ADDR_LIST="$BDX_MAIN_HOST $RASPBERRY_EPICS_HOST"
    export EPICS_CA_AUTO_ADDR_LIST=NO
    export BDX_CA_ADDR_LIST="$BDX_MAIN_HOST $RASPBERRY_EPICS_HOST"
    export BDX_CA_AUTO_ADDR_LIST=false
}

bdx_stack_load_archiver_environment() {
    local stack_main_host="$BDX_MAIN_HOST"
    local stack_epics_ca_addr_list="$EPICS_CA_ADDR_LIST"
    local stack_epics_ca_auto_addr_list="$EPICS_CA_AUTO_ADDR_LIST"
    local stack_bdx_ca_addr_list="$BDX_CA_ADDR_LIST"
    local stack_bdx_ca_auto_addr_list="$BDX_CA_AUTO_ADDR_LIST"

    bdx_load_env "$ARCHIVER_ENV_FILE"
    bdx_apply_user_layout

    export BDX_MAIN_HOST="$stack_main_host"
    export BDX_EPICS_INTERFACE="$stack_main_host"
    export EPICS_CA_ADDR_LIST="$stack_epics_ca_addr_list"
    export EPICS_CA_AUTO_ADDR_LIST="$stack_epics_ca_auto_addr_list"
    export BDX_CA_ADDR_LIST="$stack_bdx_ca_addr_list"
    export BDX_CA_AUTO_ADDR_LIST="$stack_bdx_ca_auto_addr_list"

    ARCHIVER_APP_DIR="$BDX_ARCHIVER_APP_DIR"
    ARCHIVER_MGMT_URL="$BDX_ARCHIVER_MGMT_URL"
    ARCHIVER_ENGINE_URL="$BDX_ARCHIVER_ENGINE_URL"
    ARCHIVER_ETL_URL="$BDX_ARCHIVER_ETL_URL"
    ARCHIVER_RETRIEVAL_BPL_URL="$BDX_ARCHIVER_RETRIEVAL_BPL_URL"
    ARCHIVER_RETRIEVAL_URL="$BDX_ARCHIVER_RETRIEVAL_DATA_URL"
}

bdx_stack_validate_installation() {
    [[ -d "$VENV_DIR" ]] || bdx_stack_die "Repository virtual environment not found: $VENV_DIR"
    [[ -x "$IOC_COMMAND" ]] || bdx_stack_die "bdx-prototype-ioc not found or not executable: $IOC_COMMAND"
    [[ -x "$CAPROTO_GET" ]] || bdx_stack_die "caproto-get not found or not executable: $CAPROTO_GET"
    [[ -f "$ARCHIVER_ENV_FILE" ]] || bdx_stack_die "Archiver environment file not found: $ARCHIVER_ENV_FILE"
    bdx_stack_load_archiver_environment
    [[ -d "$ARCHIVER_APP_DIR" ]] || bdx_stack_die "Archiver user-local installation not found: $ARCHIVER_APP_DIR"
    [[ -x "$ARCHIVER_HEALTHCHECK" ]] || bdx_stack_die "Archiver healthcheck not found or not executable: $ARCHIVER_HEALTHCHECK"
    [[ -x "$ARCHIVER_START" ]] || bdx_stack_die "Archiver start script not found or not executable: $ARCHIVER_START"
    [[ -x "$ARCHIVER_STATUS" ]] || bdx_stack_die "Archiver status script not found or not executable: $ARCHIVER_STATUS"
    [[ -x "$ARCHIVER_AUTOREGISTER" ]] || bdx_stack_die "Archiver auto-register script not found or not executable: $ARCHIVER_AUTOREGISTER"
    [[ -x "$PHOEBUS_LAUNCHER" ]] || bdx_stack_die "Phoebus launcher not found or not executable: $PHOEBUS_LAUNCHER"
    command -v pgrep >/dev/null 2>&1 || bdx_stack_die "pgrep is required."
    command -v curl >/dev/null 2>&1 || bdx_stack_die "curl is required."

    local list_name
    for list_name in psu.txt chiller.txt environment.txt; do
        [[ -f "$ARCHIVER_APP_DIR/pv-lists/$list_name" ]] || \
            bdx_stack_die "Required Archiver PV list not found: $ARCHIVER_APP_DIR/pv-lists/$list_name"
    done
}

bdx_stack_shell_quote() {
    printf "%q" "$1"
}

bdx_stack_escape_applescript() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    printf '%s' "$value"
}

bdx_stack_ioc_running() {
    bdx_stack_main_ioc_port_listening
}

bdx_stack_ioc_process_running() {
    pgrep -f "[b]dx-prototype-ioc|[p]ython.*bdx.*prototype.*ioc|[p]ython.*bdx_slow_control.*prototype" >/dev/null 2>&1
}

bdx_stack_python() {
    if [[ -x "$VENV_DIR/bin/python" ]]; then
        printf '%s\n' "$VENV_DIR/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        command -v python3
    else
        return 1
    fi
}

bdx_stack_main_ioc_port_listening() {
    local python_cmd
    python_cmd="$(bdx_stack_python)" || return 1
    "$python_cmd" - "$BDX_MAIN_HOST" 5064 <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
try:
    with socket.create_connection((host, port), timeout=1.0):
        pass
except OSError:
    sys.exit(1)
sys.exit(0)
PY
}

bdx_stack_wait_for_ioc_listener() {
    local timeout_seconds="${1:-60}"
    local deadline
    deadline=$((SECONDS + timeout_seconds))

    echo "Waiting for main IOC listener: $BDX_MAIN_HOST:5064"
    until bdx_stack_main_ioc_port_listening; do
        if (( SECONDS >= deadline )); then
            bdx_stack_die "Timed out waiting for main IOC listener on $BDX_MAIN_HOST:5064"
        fi
        sleep 1
    done
}

bdx_stack_start_ioc_if_needed() {
    if bdx_stack_ioc_running; then
        echo "BDX main IOC is already listening on $BDX_MAIN_HOST:5064; not starting another instance."
        return 0
    fi
    if bdx_stack_ioc_process_running; then
        bdx_stack_die "A BDX IOC process exists, but it is not listening on $BDX_MAIN_HOST:5064. Refusing to start a duplicate IOC."
    fi

    command -v osascript >/dev/null 2>&1 || \
        bdx_stack_die "osascript is required to open the IOC in a dedicated macOS Terminal window."

    local terminal_command escaped_command
    terminal_command="cd $(bdx_stack_shell_quote "$ROOT_DIR") && "
    terminal_command+="mkdir -p $(bdx_stack_shell_quote "$BDX_STACK_RUNTIME_DIR") && "
    terminal_command+="printf '%s\n' \"\$\$\" > $(bdx_stack_shell_quote "$IOC_PID_FILE") && "
    terminal_command+="export BDX_EPICS_INTERFACE=$(bdx_stack_shell_quote "$BDX_EPICS_INTERFACE") && "
    terminal_command+="export BDX_LOG_LEVEL=INFO && "
    terminal_command+="exec $(bdx_stack_shell_quote "$IOC_COMMAND")"
    escaped_command="$(bdx_stack_escape_applescript "$terminal_command")"

    echo "Starting BDX main IOC in a dedicated Terminal window."
    osascript \
        -e 'tell application "Terminal"' \
        -e "do script \"$escaped_command\"" \
        -e 'activate' \
        -e 'end tell'
    bdx_stack_wait_for_ioc_listener 90
}

bdx_stack_wait_for_pv_read() {
    local pv="$1"
    local timeout_seconds="${2:-60}"
    local deadline
    deadline=$((SECONDS + timeout_seconds))

    echo "Waiting for Channel Access PV: $pv"
    until "$CAPROTO_GET" --timeout 2 "$pv" >/dev/null 2>&1; do
        if (( SECONDS >= deadline )); then
            bdx_stack_die "Timed out waiting for Channel Access PV: $pv"
        fi
        sleep 1
    done
}

bdx_stack_archiver_status_output() {
    "$ARCHIVER_STATUS" --env "$ARCHIVER_ENV_FILE" --user-local 2>&1 || true
}

bdx_stack_archiver_running_count() {
    local status_output="$1"
    local component count=0
    for component in $(bdx_component_list); do
        if printf '%s\n' "$status_output" | grep -Eq "^${component}: running pid [0-9]+"; then
            count=$((count + 1))
        fi
    done
    printf "%s\n" "$count"
}

bdx_stack_component_ready_url() {
    bdx_component_ready_url "$1"
}

bdx_stack_archiver_ready_count() {
    local component url count=0
    for component in $(bdx_component_list); do
        url="$(bdx_stack_component_ready_url "$component")"
        if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
            count=$((count + 1))
        fi
    done
    printf "%s\n" "$count"
}

bdx_stack_archiver_first_unready_endpoint() {
    local component url
    for component in $(bdx_component_list); do
        url="$(bdx_stack_component_ready_url "$component")"
        if ! curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
            printf "%s endpoint is not ready: %s\n" "$component" "$url"
            return 0
        fi
    done
    return 1
}

bdx_stack_archiver_state() {
    local status_output running_count ready_count
    status_output="$(bdx_stack_archiver_status_output)"
    running_count="$(bdx_stack_archiver_running_count "$status_output")"
    ready_count="$(bdx_stack_archiver_ready_count)"

    if [[ "$running_count" -eq 0 && "$ready_count" -eq 0 ]]; then
        printf '%s\n' "inactive"
    elif [[ "$running_count" -eq 4 && "$ready_count" -eq 4 ]]; then
        printf '%s\n' "healthy"
    elif [[ "$running_count" -eq 4 ]]; then
        printf '%s\n' "starting"
    elif [[ "$running_count" -gt 0 && "$running_count" -lt 4 ]]; then
        printf '%s\n' "partial"
    elif [[ "$ready_count" -gt 0 ]]; then
        printf '%s\n' "inconsistent"
    else
        printf '%s\n' "inconsistent"
    fi
}

bdx_stack_any_archiver_endpoint_reachable() {
    local url
    for url in \
        "$(bdx_stack_component_ready_url mgmt)" \
        "$(bdx_stack_component_ready_url engine)" \
        "$(bdx_stack_component_ready_url etl)" \
        "$(bdx_stack_component_ready_url retrieval)"; do
        if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
            return 0
        fi
    done
    return 1
}

bdx_stack_wait_for_archiver_healthy() {
    local timeout_seconds="${1:-180}"
    local deadline
    local state unready
    deadline=$((SECONDS + timeout_seconds))

    echo "Waiting for Archiver Appliance components to become healthy."
    while true; do
        state="$(bdx_stack_archiver_state)"
        if [[ "$state" == "healthy" ]]; then
            return 0
        fi
        if (( SECONDS >= deadline )); then
            unready="$(bdx_stack_archiver_first_unready_endpoint || true)"
            if [[ -n "$unready" ]]; then
                echo "$unready" >&2
            fi
            bdx_stack_die "Timed out waiting for Archiver Appliance health."
        fi
        if [[ "$state" != "starting" && "$state" != "inactive" ]]; then
            bdx_stack_die "Archiver Appliance entered unexpected state while waiting: $state"
        fi
        unready="$(bdx_stack_archiver_first_unready_endpoint || true)"
        if [[ -n "$unready" ]]; then
            echo "$unready"
        fi
        sleep 2
    done
}

bdx_stack_ensure_archiver() {
    local state
    state="$(bdx_stack_archiver_state)"
    case "$state" in
        healthy)
            echo "Archiver Appliance is healthy; leaving it untouched."
            ;;
        starting)
            echo "Archiver Appliance processes are active; waiting for ready endpoints."
            bdx_stack_wait_for_archiver_healthy 60
            ;;
        inactive)
            echo "Archiver Appliance is inactive; starting the user-local deployment."
            BDX_MAIN_HOST="$BDX_MAIN_HOST" \
            EPICS_CA_ADDR_LIST="$EPICS_CA_ADDR_LIST" \
            EPICS_CA_AUTO_ADDR_LIST="$EPICS_CA_AUTO_ADDR_LIST" \
                "$ARCHIVER_START" --env "$ARCHIVER_ENV_FILE" --user-local
            bdx_stack_wait_for_archiver_healthy 180
            ;;
        partial)
            echo "Archiver Appliance is partially running." >&2
            echo "Status output:" >&2
            "$ARCHIVER_STATUS" --env "$ARCHIVER_ENV_FILE" --user-local >&2 || true
            echo "Healthcheck output:" >&2
            "$ARCHIVER_HEALTHCHECK" --env "$ARCHIVER_ENV_FILE" --user-local >&2 || true
            bdx_stack_die "Refusing to start duplicate Archiver Appliance processes."
            ;;
        inconsistent)
            echo "Archiver Appliance state is inconsistent: ready endpoints responded but expected PID information is missing." >&2
            echo "Status output:" >&2
            "$ARCHIVER_STATUS" --env "$ARCHIVER_ENV_FILE" --user-local >&2 || true
            echo "Healthcheck output:" >&2
            "$ARCHIVER_HEALTHCHECK" --env "$ARCHIVER_ENV_FILE" --user-local >&2 || true
            bdx_stack_die "Refusing to start duplicate Archiver Appliance processes."
            ;;
        *)
            bdx_stack_die "Unexpected Archiver Appliance state: $state"
            ;;
    esac
    bdx_stack_ensure_archiver_registration
}

bdx_stack_ensure_archiver_registration() {
    echo "Ensuring automatic Archiver PV registration retry helper is running."
    SCRIPT_DIR="$ARCHIVER_SCRIPT_DIR" bdx_archiver_start_registration_retry "$ARCHIVER_ENV_FILE" 1
}

bdx_stack_urlencode_value() {
    local value="$1"
    local python_cmd
    python_cmd="$(bdx_stack_python)" || bdx_stack_die "Python 3 is required."
    "$python_cmd" -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$value"
}

bdx_stack_archiver_pv_connected() {
    local pv="$1"
    local encoded_pv body python_cmd
    encoded_pv="$(bdx_stack_urlencode_value "$pv")"
    body="$(curl -fsS --max-time 5 "${ARCHIVER_MGMT_URL%/}/getPVStatus?pv=$encoded_pv")" || return 1
    python_cmd="$(bdx_stack_python)" || return 1
    printf '%s\n' "$body" | "$python_cmd" -c '
import json
import sys

payload = json.load(sys.stdin)
pv = sys.argv[1]

def find_status(value):
    if isinstance(value, dict):
        if pv in value and isinstance(value[pv], dict):
            return value[pv]
        return value
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            name = item.get("pvName") or item.get("pv") or item.get("name")
            if name in (None, pv):
                return item
    return {}

status = find_status(payload)
connection_state = status.get("connectionState")
connected = (
    connection_state is True
    or (
        isinstance(connection_state, str)
        and connection_state.strip().lower() == "true"
    )
)
sys.exit(0 if connected else 1)
' "$pv"
}

bdx_stack_wait_for_archiver_pv_connection() {
    local pv="$1"
    local timeout_seconds="${2:-180}"
    local deadline
    deadline=$((SECONDS + timeout_seconds))

    echo "Waiting for Archiver getPVStatus connectionState=true: $pv"
    until bdx_stack_archiver_pv_connected "$pv"; do
        if (( SECONDS >= deadline )); then
            bdx_stack_die "Timed out waiting for Archiver PV connection: $pv"
        fi
        sleep 2
    done
}

bdx_stack_launch_phoebus() {
    local display="$1"
    export BDX_STACK_RUNTIME_DIR
    export BDX_ARCHIVER_ENABLED=true
    export BDX_ARCHIVER_URL="$ARCHIVER_RETRIEVAL_URL"
    export BDX_ARCHIVER_PREFLIGHT_PV="$PHOEBUS_PREFLIGHT_PV"
    exec "$PHOEBUS_LAUNCHER" "$display"
}

bdx_stack_print_summary() {
    cat <<EOF
BDX prototype stack configuration:
  Main IOC:      $BDX_MAIN_HOST
  Main IOC source: $BDX_MAIN_HOST_SOURCE
  Raspberry IOC: $RASPBERRY_EPICS_HOST
  LV1:           $LV1_ENDPOINT
  LV2:           $LV2_ENDPOINT
  Chiller:       $CHILLER_ENDPOINT
  Archiver:      http://127.0.0.1:17665-17668
  CA list:       $BDX_CA_ADDR_LIST
EOF
}

bdx_stack_main() {
    bdx_stack_parse_args "$@"
    bdx_stack_load_runtime_environment
    bdx_stack_validate_installation
    bdx_stack_print_summary
    bdx_stack_start_ioc_if_needed
    bdx_stack_wait_for_ioc_listener 5
    bdx_stack_wait_for_pv_read "$IOC_READY_PV" 90
    bdx_stack_ensure_archiver
    bdx_stack_wait_for_archiver_pv_connection "$ARCHIVER_READY_PV" 180
    bdx_stack_launch_phoebus "$BDX_STACK_DISPLAY"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    bdx_stack_main "$@"
fi
