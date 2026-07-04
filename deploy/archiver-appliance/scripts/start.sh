#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

ENV_FILE="$(bdx_archiver_default_env_file)"
USER_LOCAL=0
FOREGROUND=0

usage() {
    cat <<'EOF'
Usage: start.sh [options]

Options:
  --env FILE       Load deployment environment from FILE.
  --user-local     Override paths with a user-local layout.
  --foreground     Keep all component Tomcats attached to this process.
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
        --foreground)
            FOREGROUND=1
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

if [[ ! -x "$BDX_ARCHIVER_TOMCAT_HOME/bin/catalina.sh" ]]; then
    bdx_die "Tomcat catalina.sh not found or not executable: $BDX_ARCHIVER_TOMCAT_HOME/bin/catalina.sh"
fi

start_component_background() {
    local component="$1"
    local base
    base="$(bdx_tomcat_base "$component")"
    if [[ ! -d "$base" ]]; then
        bdx_die "Tomcat base not configured for $component: $base"
    fi
    echo "Starting Archiver Appliance component: $component"
    CATALINA_HOME="$BDX_ARCHIVER_TOMCAT_HOME" \
    CATALINA_BASE="$base" \
    CATALINA_PID="$base/tomcat.pid" \
        "$BDX_ARCHIVER_TOMCAT_HOME/bin/catalina.sh" start
}

foreground_pids=()

stop_foreground_components() {
    local pid
    for pid in "${foreground_pids[@]:-}"; do
        if kill -0 "$pid" >/dev/null 2>&1; then
            kill "$pid" >/dev/null 2>&1 || true
        fi
    done
}

start_component_foreground() {
    local component="$1"
    local base
    base="$(bdx_tomcat_base "$component")"
    if [[ ! -d "$base" ]]; then
        bdx_die "Tomcat base not configured for $component: $base"
    fi
    echo "Starting Archiver Appliance component in foreground: $component"
    CATALINA_HOME="$BDX_ARCHIVER_TOMCAT_HOME" \
    CATALINA_BASE="$base" \
    CATALINA_PID="$base/tomcat.pid" \
        "$BDX_ARCHIVER_TOMCAT_HOME/bin/catalina.sh" run \
        >>"$BDX_ARCHIVER_LOG_DIR/$component.out" \
        2>>"$BDX_ARCHIVER_LOG_DIR/$component.err" &
    foreground_pids+=("$!")
}

if [[ "$FOREGROUND" -eq 1 ]]; then
    trap stop_foreground_components INT TERM EXIT
    for component in $(bdx_component_list); do
        start_component_foreground "$component"
    done
    wait -n "${foreground_pids[@]}"
    status=$?
    stop_foreground_components
    exit "$status"
fi

for component in $(bdx_component_list); do
    start_component_background "$component"
done
