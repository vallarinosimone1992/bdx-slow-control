#!/usr/bin/env bash

bdx_archiver_script_dir() {
    cd "$(dirname "${BASH_SOURCE[1]}")" && pwd
}

bdx_archiver_deploy_dir() {
    local script_dir
    script_dir="$(bdx_archiver_script_dir)"
    cd "$script_dir/.." && pwd
}

bdx_die() {
    echo "$*" >&2
    exit 1
}

bdx_load_env() {
    local env_file="$1"
    if [[ ! -f "$env_file" ]]; then
        bdx_die "Environment file not found: $env_file"
    fi
    set -a
    # shellcheck source=/dev/null
    source "$env_file"
    set +a
}

bdx_apply_user_layout() {
    local data_home config_home cache_home
    data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
    config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
    cache_home="${XDG_CACHE_HOME:-$HOME/.cache}"

    BDX_ARCHIVER_APP_DIR="$data_home/bdx-archiver/app"
    BDX_ARCHIVER_CONFIG_DIR="$config_home/bdx-archiver"
    BDX_ARCHIVER_STATE_DIR="$data_home/bdx-archiver/state"
    BDX_ARCHIVER_LOG_DIR="$data_home/bdx-archiver/logs"
    BDX_ARCHIVER_CACHE_DIR="$cache_home/bdx-archiver"
    BDX_ARCHIVER_TMP_DIR="$BDX_ARCHIVER_STATE_DIR/tmp"
    BDX_ARCHIVER_SHORT_TERM_DIR="$BDX_ARCHIVER_STATE_DIR/sts"
    BDX_ARCHIVER_MEDIUM_TERM_DIR="$BDX_ARCHIVER_STATE_DIR/mts"
    BDX_ARCHIVER_LONG_TERM_DIR="$BDX_ARCHIVER_STATE_DIR/lts"
    BDX_ARCHIVER_PERSISTENCE_DIR="$BDX_ARCHIVER_STATE_DIR/persistence"
    BDX_ARCHIVER_TOMCAT_HOME="$BDX_ARCHIVER_APP_DIR/tomcat"
    BDX_ARCHIVER_WAR_DIR="$BDX_ARCHIVER_APP_DIR/war"
    BDX_ARCHIVER_TOMCAT_BASE_DIR="$BDX_ARCHIVER_STATE_DIR/tomcat"
    BDX_ARCHIVER_SERVICE_MODE=user
}

bdx_require_env() {
    local name
    for name in "$@"; do
        if [[ -z "${!name:-}" ]]; then
            bdx_die "$name is required."
        fi
    done
}

bdx_archiver_default_env_file() {
    local deploy_dir
    deploy_dir="$(bdx_archiver_deploy_dir)"
    printf "%s\n" "$deploy_dir/config/archappl.env.example"
}

bdx_component_list() {
    printf "%s\n" mgmt engine etl retrieval
}

bdx_is_true() {
    case "${1:-}" in
        1|true|TRUE|True|yes|YES|Yes|on|ON|On)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

bdx_component_port() {
    case "$1" in
        mgmt) printf "%s\n" "${BDX_ARCHIVER_MGMT_PORT:-17665}" ;;
        engine) printf "%s\n" "${BDX_ARCHIVER_ENGINE_PORT:-17666}" ;;
        etl) printf "%s\n" "${BDX_ARCHIVER_ETL_PORT:-17667}" ;;
        retrieval) printf "%s\n" "${BDX_ARCHIVER_RETRIEVAL_PORT:-17668}" ;;
        *) bdx_die "Unknown Archiver Appliance component: $1" ;;
    esac
}

bdx_component_shutdown_port() {
    case "$1" in
        mgmt) printf "%s\n" 16001 ;;
        engine) printf "%s\n" 16002 ;;
        etl) printf "%s\n" 16003 ;;
        retrieval) printf "%s\n" 16004 ;;
        *) bdx_die "Unknown Archiver Appliance component: $1" ;;
    esac
}

bdx_export_archappl_env() {
    bdx_require_env \
        BDX_ARCHIVER_CONFIG_DIR \
        BDX_ARCHIVER_APPLIANCE_ID \
        BDX_ARCHIVER_SHORT_TERM_DIR \
        BDX_ARCHIVER_MEDIUM_TERM_DIR \
        BDX_ARCHIVER_LONG_TERM_DIR

    export ARCHAPPL_APPLIANCES="$BDX_ARCHIVER_CONFIG_DIR/appliances.xml"
    export ARCHAPPL_POLICIES="$BDX_ARCHIVER_CONFIG_DIR/policies.py"
    export ARCHAPPL_MYIDENTITY="$BDX_ARCHIVER_APPLIANCE_ID"
    export ARCHAPPL_SHORT_TERM_FOLDER="$BDX_ARCHIVER_SHORT_TERM_DIR"
    export ARCHAPPL_MEDIUM_TERM_FOLDER="$BDX_ARCHIVER_MEDIUM_TERM_DIR"
    export ARCHAPPL_LONG_TERM_FOLDER="$BDX_ARCHIVER_LONG_TERM_DIR"

    if [[ -n "${ARCHAPPL_PERSISTENCE_LAYER:-}" ]]; then
        export ARCHAPPL_PERSISTENCE_LAYER
    elif [[ "${BDX_ARCHIVER_EVALUATION_MODE:-false}" == "true" ]]; then
        export ARCHAPPL_PERSISTENCE_LAYER=org.epics.archiverappliance.config.persistence.InMemoryPersistence
    else
        bdx_die "ARCHAPPL_PERSISTENCE_LAYER is not configured. Use an external persistence layer for persistent deployments or set BDX_ARCHIVER_EVALUATION_MODE=true for local evaluation."
    fi

    export JAVA_OPTS="${JAVA_OPTS:-"-Xms1G -Xmx2G -ea"}"
    export EPICS_CA_ADDR_LIST="${EPICS_CA_ADDR_LIST:-}"
    export EPICS_CA_AUTO_ADDR_LIST="${EPICS_CA_AUTO_ADDR_LIST:-NO}"
    export EPICS_CA_SERVER_PORT="${EPICS_CA_SERVER_PORT:-5064}"
    export EPICS_CA_REPEATER_PORT="${EPICS_CA_REPEATER_PORT:-5065}"
}

bdx_url_join() {
    local base="$1"
    local path="$2"
    base="${base%/}"
    path="${path#/}"
    printf "%s/%s\n" "$base" "$path"
}

bdx_component_ready_url() {
    local component="$1"
    local base path
    case "$component" in
        mgmt)
            base="$BDX_ARCHIVER_MGMT_URL"
            path="${BDX_ARCHIVER_MGMT_READY_PATH:-getApplianceInfo}"
            ;;
        engine)
            base="$BDX_ARCHIVER_ENGINE_URL"
            path="${BDX_ARCHIVER_ENGINE_READY_PATH:-getApplianceInfo}"
            ;;
        etl)
            base="$BDX_ARCHIVER_ETL_URL"
            path="${BDX_ARCHIVER_ETL_READY_PATH:-getApplianceInfo}"
            ;;
        retrieval)
            base="$BDX_ARCHIVER_RETRIEVAL_BPL_URL"
            path="${BDX_ARCHIVER_RETRIEVAL_READY_PATH:-getApplianceInfo}"
            ;;
        *)
            bdx_die "Unknown Archiver Appliance component: $component"
            ;;
    esac
    bdx_url_join "$base" "$path"
}

bdx_archiver_all_components_ready() {
    local component url
    for component in $(bdx_component_list); do
        url="$(bdx_component_ready_url "$component")"
        if ! curl -fsS --max-time 5 "$url" >/dev/null; then
            echo "Archiver component is not ready: $component ($url)" >&2
            return 1
        fi
    done
}

bdx_archiver_register_pid_file() {
    printf "%s/run/auto-register-pvs.pid\n" "$BDX_ARCHIVER_STATE_DIR"
}

bdx_archiver_register_log_file() {
    printf "%s/auto-register-pvs.log\n" "$BDX_ARCHIVER_LOG_DIR"
}

bdx_archiver_resolved_pv_lists() {
    bdx_require_env BDX_ARCHIVER_APP_DIR
    local raw item
    raw="${BDX_ARCHIVER_PV_LISTS:-$BDX_ARCHIVER_APP_DIR/pv-lists/psu.txt}"
    for item in $raw; do
        [[ -n "$item" ]] || continue
        printf "%s\n" "$item"
    done
}

bdx_archiver_registration_running() {
    local pid_file pid
    pid_file="$(bdx_archiver_register_pid_file)"
    if [[ ! -f "$pid_file" ]]; then
        return 1
    fi
    pid="$(<"$pid_file")"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" >/dev/null 2>&1
}

bdx_archiver_registration_command() {
    local script_dir pv_list
    script_dir="${SCRIPT_DIR:-$(bdx_archiver_deploy_dir)/scripts}"
    printf "%q " "$script_dir/register-pvs.py" "--mgmt-url" "$BDX_ARCHIVER_MGMT_URL"
    while IFS= read -r pv_list; do
        printf "%q " "$pv_list"
    done < <(bdx_archiver_resolved_pv_lists)
    printf "\n"
}

bdx_archiver_run_registration() {
    local script_dir pv_list
    local pv_lists=()
    script_dir="${SCRIPT_DIR:-$(bdx_archiver_deploy_dir)/scripts}"
    while IFS= read -r pv_list; do
        if [[ ! -f "$pv_list" ]]; then
            echo "PV-list file not found: $pv_list" >&2
            return 1
        fi
        pv_lists+=("$pv_list")
    done < <(bdx_archiver_resolved_pv_lists)
    if [[ "${#pv_lists[@]}" -eq 0 ]]; then
        echo "No Archiver PV-list files are configured." >&2
        return 1
    fi
    "$script_dir/register-pvs.py" --mgmt-url "$BDX_ARCHIVER_MGMT_URL" "${pv_lists[@]}"
}

bdx_archiver_start_registration_retry() {
    local env_file="$1"
    local user_local="$2"
    local pid_file log_file run_dir script_dir
    local args=()

    if ! bdx_is_true "${BDX_ARCHIVER_AUTO_REGISTER:-true}"; then
        echo "Automatic Archiver PV registration is disabled."
        return 0
    fi

    pid_file="$(bdx_archiver_register_pid_file)"
    log_file="$(bdx_archiver_register_log_file)"
    run_dir="$(dirname "$pid_file")"
    script_dir="${SCRIPT_DIR:-$(bdx_archiver_deploy_dir)/scripts}"
    mkdir -p "$run_dir" "$BDX_ARCHIVER_LOG_DIR"

    if bdx_archiver_registration_running; then
        echo "Automatic Archiver PV registration is already running: $(<"$pid_file")"
        return 0
    fi
    if [[ -f "$pid_file" ]]; then
        echo "Removing stale Archiver PV registration PID file: $pid_file"
        rm -f "$pid_file"
    fi

    args=(--env "$env_file")
    if [[ "$user_local" -eq 1 ]]; then
        args+=(--user-local)
    fi

    echo "Starting automatic Archiver PV registration retry helper."
    nohup "$script_dir/auto-register-pvs.sh" "${args[@]}" >>"$log_file" 2>&1 &
    printf "%s\n" "$!" >"$pid_file"
}

bdx_archiver_stop_registration_retry() {
    local pid_file pid
    pid_file="$(bdx_archiver_register_pid_file)"
    if [[ ! -f "$pid_file" ]]; then
        return 0
    fi
    pid="$(<"$pid_file")"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" >/dev/null 2>&1; then
        echo "Stopping automatic Archiver PV registration retry helper: $pid"
        kill "$pid" >/dev/null 2>&1 || true
        for _ in {1..20}; do
            if ! kill -0 "$pid" >/dev/null 2>&1; then
                break
            fi
            sleep 0.2
        done
        if kill -0 "$pid" >/dev/null 2>&1; then
            kill -TERM "$pid" >/dev/null 2>&1 || true
        fi
    fi
    rm -f "$pid_file"
}

bdx_sha256() {
    local path="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$path" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$path" | awk '{print $1}'
    else
        bdx_die "sha256sum or shasum is required."
    fi
}

bdx_verify_checksum() {
    local path="$1"
    local expected="$2"
    local actual
    actual="$(bdx_sha256 "$path")"
    if [[ "$actual" != "$expected" ]]; then
        bdx_die "Checksum mismatch for $path: expected $expected, got $actual"
    fi
    echo "Checksum verified for $path"
}

bdx_print_package_hint() {
    cat <<'EOF'
Missing prerequisites are not installed automatically.

Ubuntu 22.04 package hints:
  sudo apt install openjdk-21-jdk curl tar
  Install Apache Tomcat 11 from the official Tomcat distribution if the OS repository does not provide Tomcat 11.

Rocky Linux 9 package hints:
  sudo dnf install java-21-openjdk java-21-openjdk-devel curl tar
  Install Apache Tomcat 11 from the official Tomcat distribution if the OS repository does not provide Tomcat 11.
EOF
}

bdx_tomcat_base() {
    printf "%s/%s\n" "$BDX_ARCHIVER_TOMCAT_BASE_DIR" "$1"
}
