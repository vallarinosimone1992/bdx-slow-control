import asyncio
import logging

import pytest

from bdx_slow_control.iocs.environment import EnvironmentalSensorIOC
from bdx_slow_control.iocs.environment import EnvironmentSummaryIOC
from bdx_slow_control.iocs.environment import TemperatureSensorIOC
from bdx_slow_control.runtime import RuntimeSettings


class SequenceSensorDriver:
    simulation = False

    def __init__(self, results):
        self.results = list(results)

    def ping(self):
        return True

    def read_value(self):
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_environment_ioc_preserves_last_value_and_recovers_status(caplog):
    async def scenario():
        driver = SequenceSensorDriver([21.5, OSError("temporary I2C failure"), 22.0])
        summary = EnvironmentSummaryIOC(
            prefix="BDX:ENV:",
            runtime_settings=RuntimeSettings(),
        )
        group = EnvironmentalSensorIOC(
            prefix="BDX:ENV:TEMP:T00:",
            driver=driver,
            unit="degC",
            sensor_kind="temperature",
            summary=summary,
            runtime_settings=RuntimeSettings(),
        )

        await group.poll_device()
        await group.mark_success()
        assert group.VALUE.value == pytest.approx(21.5)
        assert group.STATUS.value == "VALID"
        assert group.STATUS_OK.value == 1
        assert group.COMM_OK.value == "On"
        assert summary.LAST_TEMPERATURE_UPDATE.value

        with pytest.raises(OSError) as exc_info:
            await group.poll_device()
        await group.mark_failure(exc_info.value)

        assert group.VALUE.value == pytest.approx(21.5)
        assert group.STATUS.value == "DISCONNECTED"
        assert group.STATUS_OK.value == 0
        assert group.COMM_STATUS.value == "DEVICE_ERROR"
        assert group.COMM_OK.value == "Off"
        assert group.ERROR_MESSAGE.value == "temporary I2C failure"

        await group.poll_device()
        await group.mark_success()

        assert group.VALUE.value == pytest.approx(22.0)
        assert group.STATUS.value == "VALID"
        assert group.STATUS_OK.value == 1
        assert group.COMM_STATUS.value == "OK"
        assert group.COMM_OK.value == "On"
        assert group.ERROR_MESSAGE.value == ""

    caplog.set_level(logging.INFO)
    asyncio.run(scenario())

    assert "IOC poll failed" in caplog.text
    assert "IOC communication recovered" in caplog.text


def test_environment_ioc_does_not_log_repeated_identical_failures(caplog):
    async def scenario():
        group = EnvironmentalSensorIOC(
            prefix="BDX:ENV:TEMP:T00:",
            driver=SequenceSensorDriver([]),
            unit="degC",
            sensor_kind="temperature",
            runtime_settings=RuntimeSettings(),
        )
        await group.mark_failure(OSError("same I2C failure"))
        await group.mark_failure(OSError("same I2C failure"))

    caplog.set_level(logging.WARNING)
    asyncio.run(scenario())

    messages = [record.message for record in caplog.records if "IOC poll failed" in record.message]
    assert len(messages) == 1


def test_temperature_ioc_exposes_change_and_spread_diagnostics():
    async def scenario():
        runtime = RuntimeSettings()
        summary = EnvironmentSummaryIOC(prefix="BDX:ENV:", runtime_settings=runtime)
        first = TemperatureSensorIOC(
            prefix="BDX:ENV:TEMP:T00:",
            driver=SequenceSensorDriver([20.0, 23.5]),
            unit="degC",
            sensor_kind="temperature",
            sensor_name="T00",
            summary=summary,
            runtime_settings=runtime,
        )
        second = TemperatureSensorIOC(
            prefix="BDX:ENV:TEMP:T01:",
            driver=SequenceSensorDriver([24.5]),
            unit="degC",
            sensor_kind="temperature",
            sensor_name="T01",
            summary=summary,
            runtime_settings=runtime,
        )

        await first.poll_device()
        await second.poll_device()
        assert summary.TEMPERATURE_SPREAD.value == pytest.approx(4.5)

        await first.poll_device()
        assert first.CHANGE_10M.value == pytest.approx(3.5)
        assert first.CHANGE_1H.value == pytest.approx(3.5)

    asyncio.run(scenario())
