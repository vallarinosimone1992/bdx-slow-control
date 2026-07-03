import stat
from pathlib import Path


RASPBERRY_ENV = """# BDX Raspberry environment IOC runtime configuration.

BDX_EPICS_INTERFACE=172.22.50.10
BDX_LOG_LEVEL=INFO
"""

RASPBERRY_NETWORK_ENV = """# BDX Raspberry dedicated slow-control Ethernet configuration.

BDX_ETHERNET_INTERFACE=eth0
BDX_ETHERNET_CONNECTION=bdx-slow-control
BDX_ETHERNET_ADDRESS=172.22.50.10/24
"""


def test_raspberry_service_template_targets_environment_ioc_only():
    text = Path("systemd/raspberry/bdx-environment-ioc.service.in").read_text(
        encoding="utf-8"
    )
    assert (
        "bdx-environment-ioc --config "
        "/etc/bdx-slow-control/profiles/raspberry/environment.json"
    ) in text
    assert "bdx-prototype-ioc" not in text
    assert "EnvironmentFile=/etc/bdx-slow-control/bdx.env" in text
    assert "SupplementaryGroups=i2c" in text
    assert "Restart=on-failure" in text
    assert "streamdaq" not in text
    assert "User=@BDX_RUNTIME_USER@" in text
    assert "Group=@BDX_RUNTIME_GROUP@" in text


def test_raspberry_installer_does_not_enable_or_start_service():
    text = Path("scripts/install_raspberry.sh").read_text(encoding="utf-8")
    executable_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "systemctl enable bdx-environment-ioc" not in executable_lines
    assert "systemctl start bdx-environment-ioc" not in executable_lines
    assert "systemctl daemon-reload" in text
    assert 'CONFIG_DIR="/etc/bdx-slow-control"' in text
    assert "config/profiles/raspberry" in text
    assert '"$CONFIG_DIR/profiles/raspberry/"' in text
    assert "SUDO_USER" in text


def test_raspberry_profile_contains_canonical_ioc_environment():
    text = Path("config/profiles/raspberry/bdx.env").read_text(encoding="utf-8")

    assert text == RASPBERRY_ENV


def test_raspberry_network_configuration_is_repository_controlled():
    text = Path("config/deployment/raspberry-network.env").read_text(encoding="utf-8")

    assert text == RASPBERRY_NETWORK_ENV


def test_raspberry_installer_installs_canonical_environment_file():
    text = Path("scripts/install_raspberry.sh").read_text(encoding="utf-8")

    assert 'RASPBERRY_ENV="$RASPBERRY_PROFILE/bdx.env"' in text
    assert 'rsync -a --delete --exclude bdx.env "$RASPBERRY_PROFILE/"' in text
    assert 'cmp -s "$RASPBERRY_ENV" "$CONFIG_DIR/bdx.env"' in text
    assert 'cp -p "$CONFIG_DIR/bdx.env" "$backup_path"' in text
    assert 'install -m 0644 "$RASPBERRY_ENV" "$CONFIG_DIR/bdx.env"' in text
    stale_interface = "BDX_EPICS_INTERFACE=10.0.2" ".133"
    assert stale_interface not in text
    assert "set BDX_EPICS_INTERFACE" not in text


def test_raspberry_network_script_is_explicit_and_eth0_only():
    path = Path("scripts/configure_raspberry_network.sh")
    text = path.read_text(encoding="utf-8")
    mode = path.stat().st_mode

    assert mode & stat.S_IXUSR
    assert 'CONFIG_FILE="${1:-$ROOT_DIR/config/deployment/raspberry-network.env}"' in text
    assert 'source "$CONFIG_FILE"' in text
    assert "nmcli is required." in text
    assert "NetworkManager is not active." in text
    assert 'connection.interface-name "$interface"' in text
    assert "connection.autoconnect yes" in text
    assert "connection.autoconnect-priority 100" in text
    assert 'ipv4.addresses "$address"' in text
    assert 'ipv4.gateway ""' in text
    assert 'ipv4.dns ""' in text
    assert 'ipv4.routes ""' in text
    assert "ipv4.never-default yes" in text
    assert "ipv4.ignore-auto-dns yes" in text
    assert "ipv6.never-default yes" in text
    assert 'nmcli connection up "$connection"' in text
    assert "wlan0" not in text
