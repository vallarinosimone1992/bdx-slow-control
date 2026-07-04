#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${BDX_PHOEBUS_ENV:-$ROOT_DIR/phoebus/phoebus.env}"

if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
fi

BDX_CA_ADDR_LIST="${BDX_CA_ADDR_LIST:-127.0.0.1}"
BDX_CA_AUTO_ADDR_LIST="${BDX_CA_AUTO_ADDR_LIST:-false}"
BDX_CA_SERVER_PORT="${BDX_CA_SERVER_PORT:-5064}"
BDX_CA_REPEATER_PORT="${BDX_CA_REPEATER_PORT:-5065}"
BDX_PHOEBUS_UPDATE_THROTTLE_MS="${BDX_PHOEBUS_UPDATE_THROTTLE_MS:-2000}"
if [[ ! "$BDX_PHOEBUS_UPDATE_THROTTLE_MS" =~ ^[0-9]+$ ]] || \
   (( BDX_PHOEBUS_UPDATE_THROTTLE_MS <= 1000 )); then
    echo "BDX_PHOEBUS_UPDATE_THROTTLE_MS must be an integer greater than 1000 ms." >&2
    exit 2
fi
export BDX_TREND_RANGE="${BDX_TREND_RANGE:-10 minutes}"
BDX_TREND_RING_SIZE="${BDX_TREND_RING_SIZE:-5000}"
BDX_ARCHIVER_ENABLED="${BDX_ARCHIVER_ENABLED:-false}"
BDX_ARCHIVER_URL="${BDX_ARCHIVER_URL:-}"
BDX_ARCHIVER_NAME="${BDX_ARCHIVER_NAME:-BDX Archiver}"
BDX_ARCHIVER_STRICT_CHECK="${BDX_ARCHIVER_STRICT_CHECK:-false}"
BDX_ARCHIVER_PREFLIGHT_PV="${BDX_ARCHIVER_PREFLIGHT_PV:-}"

display="${1:-${BDX_PHOEBUS_DISPLAY:-overview}}"
if [[ $# -gt 0 ]]; then
    shift
fi

if [[ "$display" != *.bob ]]; then
    display="$ROOT_DIR/phoebus/displays/$display.bob"
elif [[ "$display" != /* ]]; then
    display="$ROOT_DIR/$display"
fi

if [[ ! -f "$display" ]]; then
    echo "Phoebus display not found: $display" >&2
    exit 2
fi

is_true() {
    case "$1" in
        1|true|TRUE|True|yes|YES|Yes|on|ON|On)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

strip_url_userinfo() {
    local value="$1"
    if [[ "$value" =~ ^([A-Za-z][A-Za-z0-9+.-]*://)[^/@]+@(.+)$ ]]; then
        printf '%s%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    elif [[ "$value" == *@* && "$value" != *"://"* ]]; then
        printf '%s\n' "${value#*@}"
    else
        printf '%s\n' "$value"
    fi
}

normalize_archiver_url() {
    local value
    value="$(strip_url_userinfo "$1")"
    value="${value%/}"
    case "$value" in
        "")
            return 1
            ;;
        pbraw://*)
            printf '%s\n' "$value"
            ;;
        http://*)
            printf 'pbraw://%s\n' "${value#http://}"
            ;;
        https://*)
            printf 'pbraw://%s\n' "${value#https://}"
            ;;
        *://*)
            return 1
            ;;
        *)
            printf 'pbraw://%s\n' "$value"
            ;;
    esac
}

pbraw_to_http_url() {
    local pbraw_url="$1"
    local raw_url
    raw_url="$(strip_url_userinfo "$BDX_ARCHIVER_URL")"
    if [[ "$raw_url" == https://* ]]; then
        printf 'https://%s\n' "${pbraw_url#pbraw://}"
    else
        printf 'http://%s\n' "${pbraw_url#pbraw://}"
    fi
}

archive_enabled=false
archive_urls=""
archive_retrieval="<disabled>"
archive_use_https=false
if is_true "$BDX_ARCHIVER_ENABLED"; then
    if archive_pbraw="$(normalize_archiver_url "$BDX_ARCHIVER_URL")"; then
        archive_enabled=true
        archive_urls="${archive_pbraw}|${BDX_ARCHIVER_NAME}"
        archive_retrieval="$archive_pbraw"
        if [[ "$(strip_url_userinfo "$BDX_ARCHIVER_URL")" == https://* ]]; then
            archive_use_https=true
        fi
    elif is_true "$BDX_ARCHIVER_STRICT_CHECK"; then
        echo "BDX_ARCHIVER_URL must be a valid Archiver Appliance retrieval endpoint." >&2
        exit 2
    else
        echo "Archiver is enabled, but BDX_ARCHIVER_URL is empty or invalid; using live Channel Access only." >&2
    fi
fi

runtime_dir="${XDG_RUNTIME_DIR:-$ROOT_DIR/.runtime}/bdx-phoebus"
mkdir -p "$runtime_dir"
settings="$runtime_dir/settings.ini"

cat > "$settings" <<EOF
org.phoebus.pv/default=ca
org.phoebus.pv.ca/addr_list=$BDX_CA_ADDR_LIST
org.phoebus.pv.ca/auto_addr_list=$BDX_CA_AUTO_ADDR_LIST
org.phoebus.pv.ca/server_port=$BDX_CA_SERVER_PORT
org.phoebus.pv.ca/repeater_port=$BDX_CA_REPEATER_PORT
org.phoebus.pv.ca/max_array_size=1000000
org.csstudio.display.builder.runtime/update_throttle=$BDX_PHOEBUS_UPDATE_THROTTLE_MS
org.csstudio.trends.databrowser3/urls=$archive_urls
org.csstudio.trends.databrowser3/archives=$archive_urls
org.csstudio.trends.databrowser3/use_default_archives=true
org.csstudio.trends.databrowser3/drop_failed_archives=true
org.csstudio.trends.databrowser3/live_buffer_size=$BDX_TREND_RING_SIZE
org.csstudio.trends.databrowser3/automatic_history_refresh=true
org.phoebus.archive.reader.appliance/useHttps=$archive_use_https
org.phoebus.archive.reader.appliance/useStatisticsForOptimizedData=true
org.phoebus.archive.reader.appliance/useNewOptimizedOperator=true
EOF

resolve_path() {
    local value="$1"
    local directory basename
    if [[ "$value" = /* ]]; then
        printf '%s\n' "$value"
    elif [[ -e "$value" ]]; then
        directory="$(cd "$(dirname "$value")" && pwd -P)"
        basename="$(basename "$value")"
        printf '%s/%s\n' "$directory" "$basename"
    elif [[ -e "$ROOT_DIR/$value" ]]; then
        directory="$(cd "$(dirname "$ROOT_DIR/$value")" && pwd -P)"
        basename="$(basename "$value")"
        printf '%s/%s\n' "$directory" "$basename"
    else
        printf '%s\n' "$ROOT_DIR/$value"
    fi
}

phoebus_cmd=""
if [[ -n "${BDX_PHOEBUS_CMD:-}" ]]; then
    phoebus_cmd="$(resolve_path "$BDX_PHOEBUS_CMD")"
elif [[ -n "${BDX_PHOEBUS_HOME:-}" ]]; then
    phoebus_cmd="$(resolve_path "$BDX_PHOEBUS_HOME")/phoebus.sh"
else
    candidates=(
        "$ROOT_DIR/phoebus/phoebus-product/phoebus.sh"
        "$ROOT_DIR/../preliminary_test_epics/phoebus/phoebus-product/phoebus.sh"
        "$ROOT_DIR/../phoebus/phoebus-product/phoebus.sh"
    )
    for candidate in "${candidates[@]}"; do
        if [[ -x "$candidate" ]]; then
            phoebus_cmd="$candidate"
            break
        fi
    done
    if [[ -z "$phoebus_cmd" ]] && command -v phoebus.sh >/dev/null 2>&1; then
        phoebus_cmd="$(command -v phoebus.sh)"
    elif [[ -z "$phoebus_cmd" ]] && command -v phoebus >/dev/null 2>&1; then
        phoebus_cmd="$(command -v phoebus)"
    fi
fi

echo "Phoebus settings: $settings"
echo "Phoebus display:  $display"
echo "CA address list:  $BDX_CA_ADDR_LIST"
echo "Display throttle: ${BDX_PHOEBUS_UPDATE_THROTTLE_MS} ms"
echo "Archiver enabled: $archive_enabled"
echo "Archiver name:    $BDX_ARCHIVER_NAME"
echo "Archiver retrieval: $archive_retrieval"
echo "Archiver strict check: $BDX_ARCHIVER_STRICT_CHECK"

if [[ "$archive_enabled" == true && -n "$BDX_ARCHIVER_PREFLIGHT_PV" ]]; then
    if command -v curl >/dev/null 2>&1; then
        archive_http="$(pbraw_to_http_url "$archive_retrieval")"
        preflight_url="${archive_http%/}/data/getData.json?pv=${BDX_ARCHIVER_PREFLIGHT_PV}&from=-5%20min&to=now"
        if curl -fsS --max-time 5 "$preflight_url" >/dev/null; then
            echo "Archiver preflight: ok for $BDX_ARCHIVER_PREFLIGHT_PV"
        elif is_true "$BDX_ARCHIVER_STRICT_CHECK"; then
            echo "Archiver preflight failed for $BDX_ARCHIVER_PREFLIGHT_PV." >&2
            exit 2
        else
            echo "Archiver preflight failed; launching Phoebus with live Channel Access fallback." >&2
        fi
    elif is_true "$BDX_ARCHIVER_STRICT_CHECK"; then
        echo "curl is required for strict Archiver preflight." >&2
        exit 2
    else
        echo "curl is not available; skipping Archiver preflight." >&2
    fi
fi

if [[ -n "$phoebus_cmd" && -x "$phoebus_cmd" ]]; then
    exec "$phoebus_cmd" -settings "$settings" -resource "$display" "$@"
fi

if [[ -n "${BDX_PHOEBUS_APP:-}" ]]; then
    app="$(resolve_path "$BDX_PHOEBUS_APP")"
    exec open -a "$app" --args -settings "$settings" -resource "$display" "$@"
fi

if [[ "$(uname -s)" == "Darwin" ]] && [[ -d /Applications/Phoebus.app ]]; then
    exec open -a /Applications/Phoebus.app --args \
        -settings "$settings" -resource "$display" "$@"
fi

cat >&2 <<EOF
Phoebus launcher not found.

Set one of:
  BDX_PHOEBUS_CMD=/absolute/or/relative/path/to/phoebus.sh
  BDX_PHOEBUS_HOME=/path/to/phoebus-product
  BDX_PHOEBUS_APP=/Applications/Phoebus.app

You may copy phoebus/phoebus.env.example to phoebus/phoebus.env.
EOF
exit 127
