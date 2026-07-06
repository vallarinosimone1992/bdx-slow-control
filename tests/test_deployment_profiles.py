import json
from pathlib import Path

import pytest

from bdx_slow_control import cli
from bdx_slow_control.config import (
    DEFAULT_PROFILE_DIR,
    DEFAULT_PSU_CONFIG,
    ConfigurationError,
    ServerSettings,
    load_json,
)
from bdx_slow_control.prototype import build_prototype


PROFILES = Path("config/profiles")


def test_main_server_profile_excludes_environment_ioc():
    profile = PROFILES / "main-server"
    assert not (profile / "environment.json").exists()

    pvdb, _ = build_prototype(profile)
    assert "BDX:GLOBAL:SYSTEM_STATE" in pvdb
    assert "BDX:PSU:LV1:COMM_STATUS" in pvdb
    assert "BDX:PSU:LV2:COMM_STATUS" in pvdb
    assert "BDX:HV:HV1:COMM_STATUS" in pvdb
    assert "BDX:ENV:TEMP:T00:VALUE" not in pvdb
    assert "BDX:ENV:TEMP:T01:VALUE" not in pvdb


def test_default_operational_profile_contains_only_global_and_psu():
    profile = DEFAULT_PROFILE_DIR
    assert {path.name for path in profile.glob("*.json")} == {
        "global.json",
        "psu.json",
    }

    pvdb, settings = build_prototype(profile)
    assert settings.poll_interval == 1.0
    assert "BDX:GLOBAL:SYSTEM_STATE" in pvdb
    assert "BDX:PSU:LV1:COMM_STATUS" in pvdb
    assert "BDX:PSU:LV2:COMM_STATUS" in pvdb
    assert not any(name.startswith("BDX:CHILLER:") for name in pvdb)
    assert not any(name.startswith("BDX:ENV:") for name in pvdb)
    assert not any(name.startswith("BDX:HV:") for name in pvdb)
    assert not any(name.startswith("BDX:DAQ:") for name in pvdb)


def test_default_operational_psu_profile_uses_lv_hardware_without_startup_setpoints():
    psu = load_json(DEFAULT_PSU_CONFIG)
    devices = psu["devices"]

    assert psu["server"]["poll_interval"] == 1.0
    assert [device["name"] for device in devices] == ["LV1", "LV2"]
    assert [device["prefix"] for device in devices] == [
        "BDX:PSU:LV1:",
        "BDX:PSU:LV2:",
    ]
    assert [device["mode"] for device in devices] == ["hardware", "hardware"]
    assert [device["driver"] for device in devices] == ["cpx400dp", "cpx400dp"]
    assert [device["host"] for device in devices] == [
        "172.22.50.20",
        "172.22.50.21",
    ]
    assert [device["port"] for device in devices] == [9221, 9221]
    assert all(device["channels"] == [1, 2] for device in devices)
    for device in devices:
        assert "initial_voltage" not in device
        assert "initial_current_limit" not in device
        assert "initial_ovp" not in device
        assert "initial_ocp" not in device
        assert "OUTPUT_SET" not in device


def test_operational_cli_defaults_use_default_profile(monkeypatch, capsys):
    captured = []

    def fake_build(config_dir: Path):
        captured.append(config_dir)
        return (
            {"BDX:PSU:LV1:COMM_STATUS": object()},
            ServerSettings(("127.0.0.1",), 1.0, False),
        )

    monkeypatch.setattr(cli, "build_prototype", fake_build)
    monkeypatch.setattr(cli, "run", lambda *args, **kwargs: None)

    cli.prototype_main([])
    cli.pv_list_main([])
    assert captured == [DEFAULT_PROFILE_DIR, DEFAULT_PROFILE_DIR]
    assert "BDX:PSU:LV1:COMM_STATUS" in capsys.readouterr().out


def test_psu_standalone_cli_default_uses_operational_psu_profile(monkeypatch):
    captured = []
    monkeypatch.setattr(
        cli,
        "_run",
        lambda builder_name, default_config, argv=None: captured.append(
            (builder_name, default_config, argv)
        ),
    )

    cli.psu_main([])

    assert captured == [("psu", str(DEFAULT_PSU_CONFIG), [])]


def test_raspberry_profile_contains_only_environment_ioc():
    profile = PROFILES / "raspberry"
    assert {path.name for path in profile.glob("*.json")} == {"environment.json"}


def test_duplicate_pv_names_are_rejected_across_local_subsystems(tmp_path: Path):
    config_dir = tmp_path / "profile"
    config_dir.mkdir()

    psu = load_json(PROFILES / "prototype" / "psu.json")
    hv = load_json(PROFILES / "prototype" / "hv.json")
    hv["device"]["prefix"] = psu["devices"][0]["prefix"]

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


def test_main_server_psu_profile_uses_two_cpx400dp_lv_supplies():
    psu = load_json(PROFILES / "main-server" / "psu.json")
    devices = psu["devices"]

    assert psu["server"]["poll_interval"] == 1.0
    assert [device["name"] for device in devices] == ["LV1", "LV2"]
    assert [device["prefix"] for device in devices] == [
        "BDX:PSU:LV1:",
        "BDX:PSU:LV2:",
    ]
    assert [device["driver"] for device in devices] == ["cpx400dp", "cpx400dp"]
    assert [device["host"] for device in devices] == [
        "172.22.50.20",
        "172.22.50.21",
    ]
    assert all(device["channels"] == [1, 2] for device in devices)
    assert all(device["software_limits"]["maximum_voltage"] == 60.0 for device in devices)
    assert all(device["software_limits"]["maximum_current_limit"] == 20.0 for device in devices)
    assert all(device["software_limits"]["maximum_power"] == 420.0 for device in devices)


def test_main_server_global_profile_uses_one_hertz_update_limits():
    global_config = load_json(PROFILES / "main-server" / "global.json")
    system = global_config["system"]

    assert system["initial_update_period"] == 1.0
    assert system["minimum_update_period"] == 1.0
    assert system["maximum_update_period"] == 3600.0


def test_main_server_chiller_profile_uses_ecosilver_hardware():
    chiller = load_json(PROFILES / "main-server" / "chiller.json")
    device = chiller["device"]

    assert device["name"] == "CHILLER1"
    assert device["prefix"] == "BDX:CHILLER:CHILLER1:"
    assert device["mode"] == "hardware"
    assert device["driver"] == "ecosilver_re_1225s"
    assert device["host"] == "172.22.50.60"
    assert device["port"] == 54321
    assert device["bath_temperature_command"] == "IN_PV_00"
    assert device["controlled_temperature_command"] == "IN_PV_01"
    assert device["pressure_enabled"] is False
    assert device["external_temperature_enabled"] is False
    assert device["safe_setpoint_read_command"] == "IN_SP_07"
    assert device["safe_setpoint_write_prefix"] == "OUT_SP_07"
    assert device["communication_timeout_read_command"] == "IN_SP_08"
    assert device["communication_timeout_write_prefix"] == "OUT_SP_08"
    assert "safe_mode_command" not in device
    assert "safe_mode_on_stop" not in device


def test_main_server_hv_profile_uses_genh600():
    hv = load_json(PROFILES / "main-server" / "hv.json")
    device = hv["device"]

    assert device["name"] == "HV1"
    assert device["prefix"] == "BDX:HV:HV1:"
    assert device["mode"] == "hardware"
    assert device["driver"] == "genh600"
    assert device["port"] == "/dev/ttyUSB0"
    assert device["baudrate"] == 9600
    assert device["address"] == 6
    assert device["channels"] == [1]


def test_systemd_installer_installs_selected_profile_only():
    text = Path("scripts/install_systemd.sh").read_text(encoding="utf-8")
    assert "config/*.json" not in text
    assert 'PROFILE_ROOT="$ROOT_DIR/config/profiles"' in text
    assert 'rsync -a --delete "$profile_source/" "$profile_dest/"' in text
    assert 'main-server)' in text
    assert 'prototype)' in text
