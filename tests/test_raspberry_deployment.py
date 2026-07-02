from pathlib import Path


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
