#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

ENV_FILE="$(bdx_archiver_default_env_file)"
USER_LOCAL=0
OUTPUT_DIR=""

usage() {
    cat <<'EOF'
Usage: backup-config.sh [options]

Options:
  --env FILE        Load deployment environment from FILE.
  --user-local      Override paths with a user-local layout.
  --output-dir DIR  Write backup tarball to DIR.
  -h, --help        Show this help.

This backs up configuration and metadata only. It does not claim that a live
filesystem copy of archive data is a consistent archive backup.
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
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
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

OUTPUT_DIR="${OUTPUT_DIR:-$BDX_ARCHIVER_STATE_DIR/backups}"
install -d -m 0755 "$OUTPUT_DIR"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_path="$OUTPUT_DIR/bdx-archiver-config-$timestamp.tar.gz"

tar_args=()
for path in \
    "$BDX_ARCHIVER_CONFIG_DIR" \
    "$BDX_ARCHIVER_APP_DIR/VERSION" \
    "$BDX_ARCHIVER_APP_DIR/CHECKSUMS" \
    "$BDX_ARCHIVER_APP_DIR/pv-lists" \
    "$BDX_ARCHIVER_PERSISTENCE_DIR"; do
    if [[ -e "$path" ]]; then
        tar_args+=("$path")
    fi
done

if [[ "${#tar_args[@]}" -eq 0 ]]; then
    bdx_die "No configuration or metadata paths exist to back up."
fi

tar -czf "$backup_path" "${tar_args[@]}"

cat <<EOF
Backup created: $backup_path

This backup contains configuration, PV lists, policies, topology, and local
persistence metadata when present. Archive data under STS/MTS/LTS is not backed
up by this script and requires a storage-specific consistent backup procedure.
EOF
