#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

ENV_FILE="$(bdx_archiver_default_env_file)"
USER_LOCAL=0
YES=0
PURGE_STATE=0

usage() {
    cat <<'EOF'
Usage: uninstall.sh [options]

Options:
  --env FILE     Load deployment environment from FILE.
  --user-local   Override paths with a user-local layout.
  --yes          Confirm removal.
  --purge-state  Also remove state, logs, and archive storage.
  -h, --help     Show this help.

The script stops the local Archiver Appliance wrappers and removes staged
application files. It does not remove OS packages or firewall rules.
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
        --yes)
            YES=1
            shift
            ;;
        --purge-state)
            PURGE_STATE=1
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

if [[ "$YES" -ne 1 ]]; then
    bdx_die "Refusing to uninstall without --yes."
fi

stop_args=(--env "$ENV_FILE")
if [[ "$USER_LOCAL" -eq 1 ]]; then
    stop_args+=(--user-local)
fi

"$SCRIPT_DIR/stop.sh" "${stop_args[@]}" || true

echo "Removing staged application directory: $BDX_ARCHIVER_APP_DIR"
rm -rf "$BDX_ARCHIVER_APP_DIR"

if [[ "$PURGE_STATE" -eq 1 ]]; then
    echo "Removing configuration, state, logs, and cache."
    rm -rf \
        "$BDX_ARCHIVER_CONFIG_DIR" \
        "$BDX_ARCHIVER_STATE_DIR" \
        "$BDX_ARCHIVER_LOG_DIR" \
        "$BDX_ARCHIVER_CACHE_DIR"
else
    echo "State, logs, cache, and configuration were preserved."
fi
