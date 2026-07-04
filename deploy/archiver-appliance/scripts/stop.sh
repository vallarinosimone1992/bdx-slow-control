#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

ENV_FILE="$(bdx_archiver_default_env_file)"
USER_LOCAL=0

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
        -h|--help)
            echo "Usage: stop.sh [--env FILE] [--user-local]"
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

if [[ ! -x "$BDX_ARCHIVER_TOMCAT_HOME/bin/catalina.sh" ]]; then
    bdx_die "Tomcat catalina.sh not found or not executable: $BDX_ARCHIVER_TOMCAT_HOME/bin/catalina.sh"
fi

for component in retrieval etl engine mgmt; do
    base="$(bdx_tomcat_base "$component")"
    if [[ -d "$base" ]]; then
        echo "Stopping Archiver Appliance component: $component"
        CATALINA_HOME="$BDX_ARCHIVER_TOMCAT_HOME" \
        CATALINA_BASE="$base" \
        CATALINA_PID="$base/tomcat.pid" \
            "$BDX_ARCHIVER_TOMCAT_HOME/bin/catalina.sh" stop 30 -force || true
    fi
done
