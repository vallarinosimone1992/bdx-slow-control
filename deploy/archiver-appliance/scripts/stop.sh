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

bdx_archiver_stop_registration_retry

if [[ ! -x "$BDX_ARCHIVER_TOMCAT_HOME/bin/catalina.sh" ]]; then
    bdx_die "Tomcat catalina.sh not found or not executable: $BDX_ARCHIVER_TOMCAT_HOME/bin/catalina.sh"
fi

overall=0
for component in retrieval etl engine mgmt; do
    base="$(bdx_tomcat_base "$component")"
    if [[ -d "$base" ]]; then
        pid="$(bdx_reconcile_component_pid_file "$component")"
        if [[ -n "$pid" ]]; then
            echo "Stopping Archiver Appliance component: $component pid $pid"
            CATALINA_HOME="$BDX_ARCHIVER_TOMCAT_HOME" \
            CATALINA_BASE="$base" \
            CATALINA_PID="$base/tomcat.pid" \
                "$BDX_ARCHIVER_TOMCAT_HOME/bin/catalina.sh" stop 30 -force || true
        else
            echo "Archiver Appliance component is already stopped: $component"
        fi
    fi
done

for component in retrieval etl engine mgmt; do
    remaining="$(bdx_component_pids "$component")"
    if [[ -n "$remaining" ]]; then
        echo "$component processes remain after shutdown: $remaining" >&2
        overall=1
    else
        rm -f "$(bdx_tomcat_base "$component")/tomcat.pid"
    fi
    if bdx_component_port_occupied "$component"; then
        echo "$component port remains occupied: $(bdx_component_port "$component")" >&2
        overall=1
    fi
done

exit "$overall"
