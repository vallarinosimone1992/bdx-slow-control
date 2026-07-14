#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

ENV_FILE="$(bdx_archiver_default_env_file)"
USER_LOCAL=0
EXTRA_ARGS=()

usage() {
    cat <<'EOF'
Usage: repair-archiver.sh [options] [-- repair options]

Options:
  --env FILE       Load deployment environment from FILE.
  --user-local     Override paths with a user-local layout.
  --batch-size N   Compatibility option; safe repair requires N=1.
  --queue-timeout SECONDS
                   Bound workflow-queue waits (default: 180).
  --validation-timeout SECONDS
                   Bound connection/first-event waits (default: 180).
  --repair-pv PV   Explicitly select a configured unhealthy PV for repair.
  --stop-on-first-failure
                   Preserve the previous fail-fast behavior for debugging.
  --pause-out-of-scope
                   Pause extra registrations; retain type info and history.
  --verbose        Print detailed submission, retry, and retrieval messages.
  --report-path FILE
                   Write the machine-readable JSON report to FILE.
  --retrieval-from ISO_TIME
                   Require representative samples after this time.
  --audit-only     Classify without registration or abort requests.
  -h, --help       Show this help.

The command requires all four Archiver components to be healthy. It does not
start, stop, or restart Archiver components, IOC processes, or Phoebus.
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
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            EXTRA_ARGS+=("$@")
            break
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

bdx_load_env "$ENV_FILE"
if [[ "$USER_LOCAL" -eq 1 ]]; then
    bdx_apply_user_layout
fi
bdx_export_archappl_env

audit_only=0
for argument in "${EXTRA_ARGS[@]}"; do
    [[ "$argument" == "--audit-only" ]] && audit_only=1
done
if [[ "$audit_only" -eq 0 ]]; then
    command -v flock >/dev/null 2>&1 || bdx_die "flock is required for serialized catalog repair."
    repair_run_dir="$BDX_ARCHIVER_STATE_DIR/run"
    mkdir -p "$repair_run_dir"
    exec 9>"$repair_run_dir/repair-archiver.lock"
    flock -n 9 || bdx_die "Another Archiver catalog repair is already active."
    bdx_archiver_stop_registration_retry
fi

pv_lists=()
while IFS= read -r pv_list; do
    [[ -f "$pv_list" ]] || bdx_die "PV-list file not found: $pv_list"
    pv_lists+=("$pv_list")
done < <(bdx_archiver_resolved_pv_lists)
[[ "${#pv_lists[@]}" -gt 0 ]] || bdx_die "No Archiver PV-list files are configured."

python_cmd="${BDX_ARCHIVER_PYTHON:-python3}"
retrieval_url="${BDX_ARCHIVER_RETRIEVAL_DATA_URL:-${BDX_ARCHIVER_DATA_RETRIEVAL_URL:-}}"
[[ -n "$retrieval_url" ]] || bdx_die "BDX_ARCHIVER_RETRIEVAL_DATA_URL is required."
PYTHONUNBUFFERED=1 "$python_cmd" "$SCRIPT_DIR/repair_archiver.py" \
    --mgmt-url "$BDX_ARCHIVER_MGMT_URL" \
    --retrieval-url "$retrieval_url" \
    --report-dir "$BDX_ARCHIVER_STATE_DIR/run" \
    "${EXTRA_ARGS[@]}" \
    "${pv_lists[@]}"
