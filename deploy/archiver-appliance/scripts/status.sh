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
            echo "Usage: status.sh [--env FILE] [--user-local]"
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
for component in $(bdx_component_list); do
    base="$(bdx_tomcat_base "$component")"
    pid_file="$base/tomcat.pid"
    component_pids=()
    while IFS= read -r pid; do
        [[ -n "$pid" ]] && component_pids+=("$pid")
    done < <(bdx_component_pids "$component")
    if [[ "${#component_pids[@]}" -eq 1 ]]; then
        echo "$component: running pid ${component_pids[0]}"
        if [[ ! -f "$pid_file" || "$(<"$pid_file")" != "${component_pids[0]}" ]]; then
            echo "$component: PID file missing or stale: $pid_file" >&2
        fi
    elif [[ "${#component_pids[@]}" -gt 1 ]]; then
        echo "$component: multiple processes ${component_pids[*]}" >&2
        overall=1
    else
        echo "$component: not running"
        overall=1
    fi
done

exit "$overall"
