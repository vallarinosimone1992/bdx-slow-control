from pathlib import Path

from bdx_slow_control.prototype import build_prototype


def test_aggregate_prototype_contains_all_major_subsystems():
    pvdb, _ = build_prototype(Path("config"))
    required = {
        "BDX:PSU:PSU1:COMM_STATUS",
        "BDX:CHILLER:CHILLER1:TEMPERATURE_RBV",
        "BDX:ENV:TEMP:T01:VALUE",
        "BDX:HV:HV1:COMM_STATUS",
        "BDX:DAQ:CRATE01:READY",
        "BDX:GLOBAL:SYSTEM_STATE",
    }
    assert required.issubset(pvdb)
