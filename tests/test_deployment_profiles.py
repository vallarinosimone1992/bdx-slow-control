import json
from pathlib import Path

import pytest

from bdx_slow_control.config import ConfigurationError, load_json
from bdx_slow_control.prototype import build_prototype


PROFILES = Path("config/profiles")


def test_main_server_profile_excludes_environment_ioc():
    profile = PROFILES / "main-server"
    assert not (profile / "environment.json").exists()

    pvdb, _ = build_prototype(profile)
    assert "BDX:GLOBAL:SYSTEM_STATE" in pvdb
    assert "BDX:PSU:PSU1:COMM_STATUS" in pvdb
    assert "BDX:ENV:TEMP:T00:VALUE" not in pvdb
    assert "BDX:ENV:TEMP:T01:VALUE" not in pvdb


def test_raspberry_profile_contains_only_environment_ioc():
    profile = PROFILES / "raspberry"
    assert {path.name for path in profile.glob("*.json")} == {"environment.json"}


def test_duplicate_pv_names_are_rejected_across_local_subsystems(tmp_path: Path):
    config_dir = tmp_path / "profile"
    config_dir.mkdir()

    psu = load_json(PROFILES / "prototype" / "psu.json")
    hv = load_json(PROFILES / "prototype" / "hv.json")
    hv["device"]["prefix"] = psu["device"]["prefix"]

    (config_dir / "psu.json").write_text(json.dumps(psu), encoding="utf-8")
    (config_dir / "hv.json").write_text(json.dumps(hv), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Duplicate PV names"):
        build_prototype(config_dir)


def test_main_server_service_uses_main_server_profile():
    text = Path("systemd/main-server/bdx-main-server-ioc.service.in").read_text(
        encoding="utf-8"
    )
    assert (
        "bdx-prototype-ioc --config-dir "
        "/etc/bdx-slow-control/profiles/main-server"
    ) in text
    assert "bdx-environment-ioc" not in text
    assert "streamdaq" not in text


def test_systemd_installer_installs_selected_profile_only():
    text = Path("scripts/install_systemd.sh").read_text(encoding="utf-8")
    assert "config/*.json" not in text
    assert 'PROFILE_ROOT="$ROOT_DIR/config/profiles"' in text
    assert 'rsync -a --delete "$profile_source/" "$profile_dest/"' in text
    assert 'main-server)' in text
    assert 'prototype)' in text
