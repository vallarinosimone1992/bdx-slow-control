#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

ENV_FILE="$(bdx_archiver_default_env_file)"
USER_LOCAL=0
PRINT_PATHS=0

usage() {
    cat <<'EOF'
Usage: configure.sh [options]

Options:
  --env FILE       Load deployment environment from FILE.
  --user-local     Override paths with a user-local layout.
  --print-paths    Print generated path configuration and exit.
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
        --print-paths)
            PRINT_PATHS=1
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

bdx_require_env \
    BDX_ARCHIVER_CONFIG_DIR \
    BDX_ARCHIVER_STATE_DIR \
    BDX_ARCHIVER_LOG_DIR \
    BDX_ARCHIVER_TMP_DIR \
    BDX_ARCHIVER_SHORT_TERM_DIR \
    BDX_ARCHIVER_MEDIUM_TERM_DIR \
    BDX_ARCHIVER_LONG_TERM_DIR \
    BDX_ARCHIVER_PERSISTENCE_DIR \
    BDX_ARCHIVER_TOMCAT_HOME \
    BDX_ARCHIVER_TOMCAT_BASE_DIR \
    BDX_ARCHIVER_WAR_DIR \
    BDX_ARCHIVER_APPLIANCE_ID \
    BDX_ARCHIVER_CLUSTER_HOST \
    BDX_ARCHIVER_CLUSTER_PORT \
    BDX_ARCHIVER_MGMT_URL \
    BDX_ARCHIVER_ENGINE_URL \
    BDX_ARCHIVER_ETL_URL \
    BDX_ARCHIVER_RETRIEVAL_BPL_URL \
    BDX_ARCHIVER_DATA_RETRIEVAL_URL

if [[ "$PRINT_PATHS" -eq 1 ]]; then
    cat <<EOF
ARCHAPPL_APPLIANCES=$BDX_ARCHIVER_CONFIG_DIR/appliances.xml
ARCHAPPL_POLICIES=$BDX_ARCHIVER_CONFIG_DIR/policies.py
ARCHAPPL_SHORT_TERM_FOLDER=$BDX_ARCHIVER_SHORT_TERM_DIR
ARCHAPPL_MEDIUM_TERM_FOLDER=$BDX_ARCHIVER_MEDIUM_TERM_DIR
ARCHAPPL_LONG_TERM_FOLDER=$BDX_ARCHIVER_LONG_TERM_DIR
BDX_ARCHIVER_TOMCAT_BASE_DIR=$BDX_ARCHIVER_TOMCAT_BASE_DIR
EOF
    exit 0
fi

install -d -m 0755 "$BDX_ARCHIVER_CONFIG_DIR"
install -d -m 0755 "$BDX_ARCHIVER_STATE_DIR"
install -d -m 0755 "$BDX_ARCHIVER_LOG_DIR"
install -d -m 0755 "$BDX_ARCHIVER_TMP_DIR"
install -d -m 0755 "$BDX_ARCHIVER_SHORT_TERM_DIR"
install -d -m 0755 "$BDX_ARCHIVER_MEDIUM_TERM_DIR"
install -d -m 0755 "$BDX_ARCHIVER_LONG_TERM_DIR"
install -d -m 0755 "$BDX_ARCHIVER_PERSISTENCE_DIR"
install -d -m 0755 "$BDX_ARCHIVER_TOMCAT_BASE_DIR"

cat > "$BDX_ARCHIVER_CONFIG_DIR/appliances.xml" <<EOF
<appliances>
  <appliance>
    <identity>$BDX_ARCHIVER_APPLIANCE_ID</identity>
    <cluster_inetport>$BDX_ARCHIVER_CLUSTER_HOST:$BDX_ARCHIVER_CLUSTER_PORT</cluster_inetport>
    <mgmt_url>$BDX_ARCHIVER_MGMT_URL</mgmt_url>
    <engine_url>$BDX_ARCHIVER_ENGINE_URL</engine_url>
    <etl_url>$BDX_ARCHIVER_ETL_URL</etl_url>
    <retrieval_url>$BDX_ARCHIVER_RETRIEVAL_BPL_URL</retrieval_url>
    <data_retrieval_url>$BDX_ARCHIVER_DATA_RETRIEVAL_URL</data_retrieval_url>
  </appliance>
</appliances>
EOF

install -m 0644 "$SCRIPT_DIR/../config/policies.py" "$BDX_ARCHIVER_CONFIG_DIR/policies.py"
install -m 0644 "$SCRIPT_DIR/../config/persistence.example" "$BDX_ARCHIVER_CONFIG_DIR/persistence.example"

for component in $(bdx_component_list); do
    base="$(bdx_tomcat_base "$component")"
    port="$(bdx_component_port "$component")"
    shutdown_port="$(bdx_component_shutdown_port "$component")"

    install -d -m 0755 "$base/conf" "$base/logs" "$base/temp" "$base/webapps" "$base/work"

    cat > "$base/conf/server.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<Server port="$shutdown_port" shutdown="SHUTDOWN">
  <Listener className="org.apache.catalina.startup.VersionLoggerListener" />
  <Listener className="org.apache.catalina.core.JreMemoryLeakPreventionListener" />
  <Listener className="org.apache.catalina.mbeans.GlobalResourcesLifecycleListener" />
  <Listener className="org.apache.catalina.core.ThreadLocalLeakPreventionListener" />
  <Service name="Catalina">
    <Connector address="${BDX_ARCHIVER_BIND_ADDRESS:-127.0.0.1}" port="$port" protocol="HTTP/1.1" connectionTimeout="20000" redirectPort="8443" />
    <Engine name="Catalina" defaultHost="localhost">
      <Host name="localhost" appBase="webapps" unpackWARs="true" autoDeploy="true">
        <Valve className="org.apache.catalina.valves.AccessLogValve" directory="logs" prefix="localhost_access_log" suffix=".txt" pattern="%h %l %u %t &quot;%r&quot; %s %b" />
      </Host>
    </Engine>
  </Service>
</Server>
EOF

    if [[ -f "$BDX_ARCHIVER_WAR_DIR/$component.war" ]]; then
        install -m 0644 "$BDX_ARCHIVER_WAR_DIR/$component.war" "$base/webapps/$component.war"
    else
        echo "WAR file not found yet for $component: $BDX_ARCHIVER_WAR_DIR/$component.war" >&2
    fi
done

cat <<EOF
Archiver Appliance configuration generated:
  $BDX_ARCHIVER_CONFIG_DIR/appliances.xml
  $BDX_ARCHIVER_CONFIG_DIR/policies.py
  $BDX_ARCHIVER_TOMCAT_BASE_DIR/{mgmt,engine,etl,retrieval}
EOF
