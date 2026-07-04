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
