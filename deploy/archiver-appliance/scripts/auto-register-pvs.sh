#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

ENV_FILE="$(bdx_archiver_default_env_file)"
USER_LOCAL=0
PRINT_COMMAND=0
ONCE=0

usage() {
    cat <<'EOF'
Usage: auto-register-pvs.sh [options]

Options:
  --env FILE        Load deployment environment from FILE.
  --user-local      Override paths with a user-local layout.
  --print-command   Print the register-pvs.py command and exit.
  --once            Try readiness and registration once instead of retrying.
  -h, --help        Show this help.
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
        --print-command)
            PRINT_COMMAND=1
            shift
            ;;
        --once)
            ONCE=1
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
bdx_export_archappl_env

if [[ "$PRINT_COMMAND" -eq 1 ]]; then
    bdx_archiver_registration_command
    exit 0
fi

retry_seconds="${BDX_ARCHIVER_REGISTER_RETRY_SECONDS:-30}"
if [[ ! "$retry_seconds" =~ ^[0-9]+$ ]] || (( retry_seconds < 1 )); then
    bdx_die "BDX_ARCHIVER_REGISTER_RETRY_SECONDS must be a positive integer."
fi

trap 'exit 0' INT TERM

while true; do
    if bdx_archiver_all_components_ready; then
        echo "Archiver components are healthy; registering BDX PVs."
        if bdx_archiver_run_registration; then
            echo "Automatic Archiver PV registration completed successfully."
            exit 0
        fi
        echo "Automatic Archiver PV registration failed; retrying in ${retry_seconds} s." >&2
    else
        echo "Archiver components are not ready; retrying in ${retry_seconds} s." >&2
    fi

    if [[ "$ONCE" -eq 1 ]]; then
        exit 1
    fi
    sleep "$retry_seconds"
done
