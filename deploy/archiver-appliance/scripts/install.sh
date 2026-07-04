#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

ENV_FILE="$(bdx_archiver_default_env_file)"
USER_LOCAL=0
DOWNLOAD=0
CHECK_ONLY=0
PRINT_CONFIG=0
VERIFY_FILE=""
VERIFY_EXPECTED=""

usage() {
    cat <<'EOF'
Usage: install.sh [options]

Options:
  --env FILE                 Load deployment environment from FILE.
  --user-local               Override paths with a user-local layout.
  --download                 Download the pinned official release artifact.
  --check-only               Check prerequisites and print planned paths only.
  --print-config             Print the effective path configuration.
  --verify-checksum FILE SHA Verify FILE against SHA and exit.
  -h, --help                 Show this help.

This script never installs operating-system packages and never starts systemd
services. Runtime data and downloaded artifacts are kept outside the Git
checkout.
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
        --download)
            DOWNLOAD=1
            shift
            ;;
        --check-only)
            CHECK_ONLY=1
            shift
            ;;
        --print-config)
            PRINT_CONFIG=1
            shift
            ;;
        --verify-checksum)
            VERIFY_FILE="$2"
            VERIFY_EXPECTED="$3"
            shift 3
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

if [[ -n "$VERIFY_FILE" ]]; then
    bdx_verify_checksum "$VERIFY_FILE" "$VERIFY_EXPECTED"
    exit 0
fi

bdx_load_env "$ENV_FILE"
if [[ "$USER_LOCAL" -eq 1 ]]; then
    bdx_apply_user_layout
fi

bdx_require_env \
    BDX_ARCHIVER_RELEASE_ARTIFACT \
    BDX_ARCHIVER_RELEASE_URL \
    BDX_ARCHIVER_RELEASE_SHA256 \
    BDX_ARCHIVER_APP_DIR \
    BDX_ARCHIVER_CONFIG_DIR \
    BDX_ARCHIVER_STATE_DIR \
    BDX_ARCHIVER_LOG_DIR \
    BDX_ARCHIVER_CACHE_DIR \
    BDX_ARCHIVER_TOMCAT_HOME \
    BDX_ARCHIVER_WAR_DIR \
    BDX_ARCHIVER_TOMCAT_BASE_DIR

print_config() {
    cat <<EOF
BDX_ARCHIVER_APP_DIR=$BDX_ARCHIVER_APP_DIR
BDX_ARCHIVER_CONFIG_DIR=$BDX_ARCHIVER_CONFIG_DIR
BDX_ARCHIVER_STATE_DIR=$BDX_ARCHIVER_STATE_DIR
BDX_ARCHIVER_LOG_DIR=$BDX_ARCHIVER_LOG_DIR
BDX_ARCHIVER_CACHE_DIR=$BDX_ARCHIVER_CACHE_DIR
BDX_ARCHIVER_TOMCAT_HOME=$BDX_ARCHIVER_TOMCAT_HOME
BDX_ARCHIVER_WAR_DIR=$BDX_ARCHIVER_WAR_DIR
BDX_ARCHIVER_TOMCAT_BASE_DIR=$BDX_ARCHIVER_TOMCAT_BASE_DIR
EOF
}

if [[ "$PRINT_CONFIG" -eq 1 ]]; then
    print_config
    exit 0
fi

missing=0
for command_name in java jar curl tar; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "Missing prerequisite command: $command_name" >&2
        missing=1
    fi
done

if command -v java >/dev/null 2>&1; then
    java_version="$(java -version 2>&1 | awk -F '"' '/version/ {print $2; exit}')"
    java_major="${java_version%%.*}"
    if [[ "$java_major" == "1" ]]; then
        java_major="$(printf "%s\n" "$java_version" | awk -F. '{print $2}')"
    fi
    if [[ -n "$java_major" && "$java_major" =~ ^[0-9]+$ && "$java_major" -lt 21 ]]; then
        echo "Java 21 or later is required; found Java $java_version." >&2
        missing=1
    fi
fi

if [[ ! -x "$BDX_ARCHIVER_TOMCAT_HOME/bin/catalina.sh" &&
      -z "${BDX_ARCHIVER_TOMCAT_TARBALL:-}" ]]; then
    echo "Tomcat 11 is required. Set BDX_ARCHIVER_TOMCAT_HOME or BDX_ARCHIVER_TOMCAT_TARBALL." >&2
    missing=1
fi

if [[ -n "${BDX_ARCHIVER_TOMCAT_TARBALL:-}" &&
      ! -f "$BDX_ARCHIVER_TOMCAT_TARBALL" ]]; then
    echo "Configured Tomcat tarball does not exist: $BDX_ARCHIVER_TOMCAT_TARBALL" >&2
    missing=1
fi

if [[ "$missing" -ne 0 ]]; then
    bdx_print_package_hint >&2
    exit 2
fi

print_config

if [[ "$CHECK_ONLY" -eq 1 ]]; then
    exit 0
fi

install -d -m 0755 "$BDX_ARCHIVER_APP_DIR"
install -d -m 0755 "$BDX_ARCHIVER_APP_DIR/scripts"
install -d -m 0755 "$BDX_ARCHIVER_APP_DIR/systemd"
install -d -m 0755 "$BDX_ARCHIVER_APP_DIR/pv-lists"
install -d -m 0755 "$BDX_ARCHIVER_CONFIG_DIR"
install -d -m 0755 "$BDX_ARCHIVER_STATE_DIR"
install -d -m 0755 "$BDX_ARCHIVER_LOG_DIR"
install -d -m 0755 "$BDX_ARCHIVER_CACHE_DIR"
install -d -m 0755 "$BDX_ARCHIVER_WAR_DIR"

if [[ ! -x "$BDX_ARCHIVER_TOMCAT_HOME/bin/catalina.sh" &&
      -n "${BDX_ARCHIVER_TOMCAT_TARBALL:-}" ]]; then
    install -d -m 0755 "$BDX_ARCHIVER_TOMCAT_HOME"
    tar -xzf "$BDX_ARCHIVER_TOMCAT_TARBALL" -C "$BDX_ARCHIVER_TOMCAT_HOME" --strip-components=1
fi

install -m 0644 "$SCRIPT_DIR/../config/archappl.env.example" "$BDX_ARCHIVER_CONFIG_DIR/archappl.env.example"
install -m 0644 "$SCRIPT_DIR/../config/appliances.xml" "$BDX_ARCHIVER_CONFIG_DIR/appliances.xml.template"
install -m 0644 "$SCRIPT_DIR/../config/policies.py" "$BDX_ARCHIVER_CONFIG_DIR/policies.py"
install -m 0644 "$SCRIPT_DIR/../config/persistence.example" "$BDX_ARCHIVER_CONFIG_DIR/persistence.example"
install -m 0644 "$SCRIPT_DIR/../VERSION" "$BDX_ARCHIVER_APP_DIR/VERSION"
install -m 0644 "$SCRIPT_DIR/../CHECKSUMS" "$BDX_ARCHIVER_APP_DIR/CHECKSUMS"
cp -R "$SCRIPT_DIR/../pv-lists/." "$BDX_ARCHIVER_APP_DIR/pv-lists/"
cp -R "$SCRIPT_DIR/." "$BDX_ARCHIVER_APP_DIR/scripts/"
cp -R "$SCRIPT_DIR/../systemd/." "$BDX_ARCHIVER_APP_DIR/systemd/"

artifact_path="$BDX_ARCHIVER_CACHE_DIR/$BDX_ARCHIVER_RELEASE_ARTIFACT"
if [[ "$DOWNLOAD" -eq 1 ]]; then
    if [[ ! -f "$artifact_path" ]]; then
        echo "Downloading $BDX_ARCHIVER_RELEASE_URL"
        curl -fL "$BDX_ARCHIVER_RELEASE_URL" -o "$artifact_path"
    else
        echo "Using existing downloaded artifact: $artifact_path"
    fi
    bdx_verify_checksum "$artifact_path" "$BDX_ARCHIVER_RELEASE_SHA256"
    tar -xzf "$artifact_path" -C "$BDX_ARCHIVER_WAR_DIR"
else
    cat <<EOF

The official release archive was not downloaded.
To download and verify it later, run:
  $0 --env "$ENV_FILE" --download
EOF
fi

cat <<EOF

Installation staging complete.
Next steps:
  1. Copy and edit $BDX_ARCHIVER_CONFIG_DIR/archappl.env.example as $BDX_ARCHIVER_CONFIG_DIR/archappl.env.
  2. Configure persistent storage and ARCHAPPL_PERSISTENCE_LAYER.
  3. Run configure.sh with the final environment file.
  4. Install the appropriate systemd unit manually when ready.
EOF
