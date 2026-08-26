from pathlib import Path

from bdx_slow_control.prototype import build_prototype


def test_aggregate_prototype_contains_all_major_subsystems():
    pvdb, _ = build_prototype(Path("config/profiles/prototype"))
    required = {
        "BDX:PSU:LV1:COMM_STATUS",
        "BDX:CHILLER:CHILLER1:TEMPERATURE_RBV",
        "BDX:ENV:TEMP:T01:VALUE",
        "BDX:HV:HV1:COMM_STATUS",
        "BDX:DAQ:CRATE01:READY",
        "BDX:DAQ:CRATE01:MEASUREMENT_ACTIVE",
        "BDX:DAQ:CRATE01:RUN_ID_RBV",
        "BDX:DAQ:CRATE01:EXPECTED_STATE_RBV",
        "BDX:GLOBAL:SYSTEM_STATE",
        "BDX:ARCHIVER:STATUS",
        "BDX:CHILLER:CHILLER1:STAT_LOW_LEVEL",
        "BDX:PSU:LV1:CH1:OCP_ALARM",
        "BDX:ARCHIVER:OK",
    }
    assert required.issubset(pvdb)
