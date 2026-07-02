import json
from pathlib import Path

from bdx_slow_control.builders import build_psu


def test_psu_pv_contract():
    config = json.loads(
        Path("config/profiles/prototype/psu.json").read_text(encoding="utf-8")
    )
    pvdb, _ = build_psu(config)
    expected = {
        "BDX:PSU:PSU1:ALLOFF_CMD",
        "BDX:PSU:PSU1:CH1:VOLTAGE_SET",
        "BDX:PSU:PSU1:CH1:VOLTAGE_RBV",
        "BDX:PSU:PSU1:CH1:OUTPUT_SET",
        "BDX:PSU:PSU1:CH1:OUTPUT_RBV",
    }
    assert expected.issubset(pvdb)
