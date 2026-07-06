#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

ENV_FILE="$(bdx_archiver_default_env_file)"
USER_LOCAL=0
CHECK_PV=0

usage() {
    cat <<'EOF'
Usage: healthcheck.sh [options]

Options:
  --env FILE       Load deployment environment from FILE.
  --user-local     Override paths with a user-local layout.
  --check-pv       Also check BDX_ARCHIVER_KNOWN_PV retrieval.
  -h, --help       Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)
            ENV_FILE="$2"
            shift 2
            ;;
        --user-local)
            USER_LOCAL=1
            shift
            ;;
        --check-pv)
            CHECK_PV=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            bdx_die "Unknown option: $1"
            ;;
    esac
done

bdx_load_env "$ENV_FILE"
if [[ "$USER_LOCAL" -eq 1 ]]; then
    bdx_apply_user_layout
fi

overall=0

check_url() {
    local name="$1"
    local url="$2"
    if curl -fsS --max-time 5 "$url" >/dev/null; then
        echo "$name endpoint is reachable: $url"
    else
        echo "$name endpoint is not reachable: $url" >&2
        overall=1
    fi
}

check_writable() {
    local name="$1"
    local path="$2"
    if [[ -d "$path" && -w "$path" ]]; then
        echo "$name storage is writable: $path"
    else
        echo "$name storage is not writable: $path" >&2
        overall=1
    fi
}

check_url mgmt "$(bdx_component_ready_url mgmt)"
check_url engine "$(bdx_component_ready_url engine)"
check_url etl "$(bdx_component_ready_url etl)"
check_url retrieval "$(bdx_component_ready_url retrieval)"

check_writable short-term "$BDX_ARCHIVER_SHORT_TERM_DIR"
check_writable medium-term "$BDX_ARCHIVER_MEDIUM_TERM_DIR"
check_writable long-term "$BDX_ARCHIVER_LONG_TERM_DIR"
check_writable logs "$BDX_ARCHIVER_LOG_DIR"

status_args=(--env "$ENV_FILE")
if [[ "$USER_LOCAL" -eq 1 ]]; then
    status_args+=(--user-local)
fi

if "$SCRIPT_DIR/status.sh" "${status_args[@]}"; then
    echo "Archiver Appliance component processes are active."
else
    echo "One or more Archiver Appliance component processes are inactive." >&2
    overall=1
fi

if [[ "$CHECK_PV" -eq 1 ]]; then
    if [[ -z "${BDX_ARCHIVER_KNOWN_PV:-}" ]]; then
        echo "BDX_ARCHIVER_KNOWN_PV is not configured." >&2
        overall=1
    elif "$SCRIPT_DIR/verify-retrieval.py" \
        --retrieval-url "$BDX_ARCHIVER_RETRIEVAL_DATA_URL" \
        --pv "$BDX_ARCHIVER_KNOWN_PV" \
        --allow-no-samples; then
        echo "Known BDX PV retrieval check completed: $BDX_ARCHIVER_KNOWN_PV"
    else
        overall=1
    fi
fi

exit "$overall"
