from pathlib import Path

from bdx_slow_control.builders import build_chiller, build_hv, build_psu
from bdx_slow_control.config import load_json


def test_psu_pv_contract():
    config = load_json(Path("config/profiles/prototype/psu.json"))
    pvdb, _ = build_psu(config)
    expected = {
        "BDX:PSU:LV1:ALLOFF_CMD",
        "BDX:PSU:LV1:CH1:VOLTAGE_SET",
        "BDX:PSU:LV1:CH1:VOLTAGE_RBV",
        "BDX:PSU:LV1:CH1:OUTPUT_SET",
        "BDX:PSU:LV1:CH1:OUTPUT_RBV",
        "BDX:PSU:LV2:ALLOFF_CMD",
        "BDX:PSU:LV2:CH1:VOLTAGE_SET",
        "BDX:PSU:LV2:CH2:CURRENT_RBV",
    }
    assert expected.issubset(pvdb)


def test_hv_pv_contract():
    config = load_json(Path("config/profiles/prototype/hv.json"))
    pvdb, _ = build_hv(config)
    expected = {
        "BDX:HV:HV1:ALLOFF_CMD",
        "BDX:HV:HV1:CH1:VOLTAGE_SET",
        "BDX:HV:HV1:CH1:VOLTAGE_RBV",
        "BDX:HV:HV1:CH1:OUTPUT_SET",
        "BDX:HV:HV1:CH1:OUTPUT_RBV",
    }
    assert expected.issubset(pvdb)


def test_chiller_pv_contract():
    config = load_json(Path("config/profiles/prototype/chiller.json"))
    pvdb, _ = build_chiller(config)
    expected = {
        "BDX:CHILLER:CHILLER1:TEMPERATURE_RBV",
        "BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV",
        "BDX:CHILLER:CHILLER1:CONTROLLED_TEMPERATURE_RBV",
        "BDX:CHILLER:CHILLER1:EXTERNAL_TEMPERATURE_RBV",
        "BDX:CHILLER:CHILLER1:PRESSURE_RBV",
        "BDX:CHILLER:CHILLER1:SETPOINT_SET",
        "BDX:CHILLER:CHILLER1:RUN_SET",
        "BDX:CHILLER:CHILLER1:DEVICE_STATUS",
        "BDX:CHILLER:CHILLER1:FAULT_DIAGNOSIS",
    }
    assert expected.issubset(pvdb)


def test_psu_legacy_single_device_config_is_still_supported():
    config = {
        "server": {"interfaces": ["0.0.0.0"], "poll_interval": 5.0},
        "device": {
            "name": "LV1",
            "prefix": "BDX:PSU:LV1:",
            "mode": "simulation",
            "channels": [1, 2],
        },
    }
    pvdb, _ = build_psu(config)

    assert "BDX:PSU:LV1:CH2:VOLTAGE_SET" in pvdb
