import asyncio

from bdx_slow_control.drivers.simulated import SimulatedDaqCrateDriver
from bdx_slow_control.iocs.daq import DaqCrateIOC
from bdx_slow_control.runtime import RuntimeSettings


def test_daq_run_state_tracks_measurement_and_run_metadata():
    async def scenario():
        group = DaqCrateIOC(
            prefix="BDX:DAQ:CRATE01:",
            driver=SimulatedDaqCrateDriver("prototype-default"),
            runtime_settings=RuntimeSettings(),
        )

        await group.RUN_ID_SET.write(value="run-42")
        await group.EXPECTED_STATE_SET.write(value="RUNNING")
        await group.STATE_SET.write(value="RUNNING")

        assert group.RUN_ID_RBV.value == "run-42"
        assert group.EXPECTED_STATE_RBV.value == "RUNNING"
        assert group.STATE_RBV.value == "RUNNING"
        assert group.MEASUREMENT_ACTIVE.value == "On"
        assert group.RUN_START_TIME.value
        assert not group.RUN_END_TIME.value

        await group.STATE_SET.write(value="STANDBY")

        assert group.MEASUREMENT_ACTIVE.value == "Off"
        assert group.RUN_END_TIME.value

    asyncio.run(scenario())
