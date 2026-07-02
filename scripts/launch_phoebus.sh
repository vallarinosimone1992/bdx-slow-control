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
org.csstudio.trends.databrowser3/urls=
org.csstudio.trends.databrowser3/archives=
org.csstudio.trends.databrowser3/use_default_archives=false
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
