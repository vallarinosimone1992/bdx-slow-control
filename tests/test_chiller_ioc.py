import asyncio
import math

import pytest

from bdx_slow_control.drivers.base import ChillerState
from bdx_slow_control.iocs.chiller import ChillerIOC
from bdx_slow_control.runtime import RuntimeSettings


class RecordingChillerDriver:
    simulation = False

    def __init__(self):
        self.calls = []
        self.state = ChillerState(
            temperature_c=20.4,
            setpoint_c=20.0,
            pressure_bar=math.nan,
            running=False,
            fault=False,
            bath_temperature_c=20.2,
            controlled_temperature_c=20.4,
            external_temperature_c=math.nan,
            pump_stage="2",
            cooling_mode="AUTO",
            safe_mode_status="AVAILABLE",
            standby_status="1",
            device_status="OK",
            fault_diagnosis="0000",
            pressure_enabled=False,
            pressure_valid=False,
            external_temperature_enabled=False,
            external_temperature_valid=False,
            safe_setpoint_c=18.0,
            communication_timeout_s=10.0,
        )

    def ping(self):
        return True

    def read_state(self):
        self.calls.append(("read_state",))
        return self.state

    def set_setpoint(self, value_c):
        self.calls.append(("set_setpoint", value_c))
        self.state = ChillerState(
            **{
                **self.state.__dict__,
                "setpoint_c": float(value_c),
            }
        )

    def set_running(self, running):
        self.calls.append(("set_running", running))


def _group(driver=None):
    return ChillerIOC(
        prefix="BDX:CHILLER:CHILLER1:",
        driver=driver or RecordingChillerDriver(),
        runtime_settings=RuntimeSettings(),
        minimum_setpoint_c=5.0,
        maximum_setpoint_c=40.0,
        warning_deviation_c=0.2,
        alarm_deviation_c=0.5,
    )


def test_chiller_poll_updates_deviation_and_optional_validity():
    async def scenario():
        driver = RecordingChillerDriver()
        group = _group(driver)

        await group.poll_device()

        assert group.TEMPERATURE_DEVIATION_RBV.value == pytest.approx(0.4)
        assert group.DEVIATION_WARNING.value == "On"
        assert group.DEVIATION_ALARM.value == "Off"
        assert group.DEVIATION_STATUS.value == "WARNING"
        assert group.PRESSURE_ENABLED.value == "Off"
        assert group.PRESSURE_VALID.value == "Off"
        assert group.EXTERNAL_TEMPERATURE_ENABLED.value == "Off"
        assert group.EXTERNAL_TEMPERATURE_VALID.value == "Off"

    asyncio.run(scenario())


def test_chiller_setpoint_request_does_not_change_hardware_before_apply():
    async def scenario():
        driver = RecordingChillerDriver()
        group = _group(driver)

        await group.SETPOINT_REQUEST.write(value=21.0)

        assert driver.calls == []

    asyncio.run(scenario())


def test_chiller_invalid_apply_performs_no_write():
    async def scenario():
        driver = RecordingChillerDriver()
        group = _group(driver)
        await group.SETPOINT_REQUEST.write(value=4.9)

        await group.APPLY_SETPOINT_CMD.write(value=True)

        assert not any(call[0] == "set_setpoint" for call in driver.calls)
        assert group.APPLY_STATUS.value == "REJECTED"

    asyncio.run(scenario())


def test_chiller_successful_apply_writes_and_refreshes_readback():
    async def scenario():
        driver = RecordingChillerDriver()
        group = _group(driver)
        await group.SETPOINT_REQUEST.write(value=21.0)

        await group.APPLY_SETPOINT_CMD.write(value=True)

        assert ("set_setpoint", 21.0) in driver.calls
        assert group.SETPOINT_RBV.value == pytest.approx(21.0)
        assert group.APPLY_STATUS.value == "APPLIED"

    asyncio.run(scenario())
