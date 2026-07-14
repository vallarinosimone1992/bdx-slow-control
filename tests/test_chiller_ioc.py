import asyncio
import math
import threading
import time

import pytest

from bdx_slow_control.drivers.base import ChillerState
from bdx_slow_control.iocs.chiller import ChillerIOC
from bdx_slow_control.iocs.power import PowerDeviceIOC
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
        self.calls.append(("ping",))
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

    def set_safe_setpoint(self, value_c):
        self.calls.append(("set_safe_setpoint", value_c))

    def set_communication_timeout(self, value_s):
        self.calls.append(("set_communication_timeout", value_s))


class SlowReadChillerDriver(RecordingChillerDriver):
    def __init__(self, delay_s=0.15):
        super().__init__()
        self.delay_s = delay_s
        self.active_reads = 0
        self.max_active_reads = 0
        self._lock = threading.Lock()

    def read_state(self):
        with self._lock:
            self.active_reads += 1
            self.max_active_reads = max(self.max_active_reads, self.active_reads)
        try:
            time.sleep(self.delay_s)
            return super().read_state()
        finally:
            with self._lock:
                self.active_reads -= 1


class SlowSetpointChillerDriver(RecordingChillerDriver):
    def set_setpoint(self, value_c):
        time.sleep(0.15)
        super().set_setpoint(value_c)


class RecordingPowerDeviceDriver:
    simulation = False

    def __init__(self):
        self.calls = []

    def ping(self):
        self.calls.append(("ping",))
        return True

    def all_outputs_off(self):
        self.calls.append(("all_outputs_off",))
        return True

    def all_off(self):
        self.calls.append(("all_off",))


def _group(driver=None, runtime_settings=None):
    return ChillerIOC(
        prefix="BDX:CHILLER:CHILLER1:",
        driver=driver or RecordingChillerDriver(),
        runtime_settings=runtime_settings or RuntimeSettings(),
        minimum_setpoint_c=5.0,
        maximum_setpoint_c=40.0,
        warning_deviation_c=0.2,
        alarm_deviation_c=0.5,
    )


async def _count_event_loop_ticks(duration_s: float) -> int:
    count = 0
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        count += 1
        await asyncio.sleep(0.01)
    return count


def test_chiller_poll_updates_deviation_and_optional_validity():
    async def scenario():
        driver = RecordingChillerDriver()
        group = _group(driver)
        try:
            await group.poll_device()

            assert group.BATH_TEMPERATURE_RBV.value == pytest.approx(20.2)
            assert group.CONTROLLED_TEMPERATURE_RBV.value == pytest.approx(20.4)
            assert group.TEMPERATURE_DEVIATION_RBV.value == pytest.approx(0.4)
            assert group.DEVIATION_WARNING.value == "On"
            assert group.DEVIATION_ALARM.value == "Off"
            assert group.DEVIATION_STATUS.value == "WARNING"
            assert group.PRESSURE_ENABLED.value == "Off"
            assert group.PRESSURE_VALID.value == "Off"
            assert group.EXTERNAL_TEMPERATURE_ENABLED.value == "Off"
            assert group.EXTERNAL_TEMPERATURE_VALID.value == "Off"
        finally:
            group.close()

    asyncio.run(scenario())


def test_chiller_setpoint_request_does_not_change_hardware_before_apply():
    async def scenario():
        driver = RecordingChillerDriver()
        group = _group(driver)
        try:
            await group.SETPOINT_REQUEST.write(value=21.0)

            assert driver.calls == []
        finally:
            group.close()

    asyncio.run(scenario())


def test_chiller_invalid_apply_performs_no_write():
    async def scenario():
        driver = RecordingChillerDriver()
        group = _group(driver)
        try:
            await group.SETPOINT_REQUEST.write(value=4.9)

            await group.APPLY_SETPOINT_CMD.write(value=True)

            assert not any(call[0] == "set_setpoint" for call in driver.calls)
            assert group.APPLY_STATUS.value == "REJECTED"
        finally:
            group.close()

    asyncio.run(scenario())


def test_chiller_successful_apply_writes_and_refreshes_readback():
    async def scenario():
        driver = RecordingChillerDriver()
        group = _group(driver)
        try:
            await group.SETPOINT_REQUEST.write(value=21.0)

            await group.APPLY_SETPOINT_CMD.write(value=True)

            assert ("set_setpoint", 21.0) in driver.calls
            assert group.SETPOINT_RBV.value == pytest.approx(21.0)
            assert group.APPLY_STATUS.value == "APPLIED"
        finally:
            group.close()

    asyncio.run(scenario())


def test_chiller_startup_poll_performs_no_control_writes():
    async def scenario():
        driver = RecordingChillerDriver()
        group = _group(driver)
        try:
            await group.check_driver_communication()
            await group.poll_device()

            assert driver.calls == [("ping",), ("read_state",)]
        finally:
            group.close()

    asyncio.run(scenario())


def test_psu_and_chiller_share_default_runtime_update_period():
    runtime = RuntimeSettings()
    psu = PowerDeviceIOC(
        prefix="BDX:PSU:LV1:",
        driver=RecordingPowerDeviceDriver(),
        runtime_settings=runtime,
    )
    chiller = _group(runtime_settings=runtime)
    try:
        assert psu.runtime_settings is runtime
        assert chiller.runtime_settings is runtime
        assert psu.poll_period == pytest.approx(1.0)
        assert chiller.poll_period == pytest.approx(1.0)
    finally:
        chiller.close()


def test_global_update_period_change_affects_psu_and_chiller_polling():
    runtime = RuntimeSettings()
    psu = PowerDeviceIOC(
        prefix="BDX:PSU:LV1:",
        driver=RecordingPowerDeviceDriver(),
        runtime_settings=runtime,
    )
    chiller = _group(runtime_settings=runtime)
    try:
        runtime.set_update_period(2.0)

        assert psu.poll_period == pytest.approx(2.0)
        assert chiller.poll_period == pytest.approx(2.0)
    finally:
        chiller.close()


def test_slow_chiller_poll_does_not_block_unrelated_asyncio_activity():
    async def scenario():
        driver = SlowReadChillerDriver()
        group = _group(driver)
        try:
            _, tick_count = await asyncio.gather(
                group.poll_device(),
                _count_event_loop_ticks(0.10),
            )

            assert tick_count >= 3
        finally:
            group.close()

    asyncio.run(scenario())


def test_chiller_poll_operations_do_not_overlap():
    async def scenario():
        driver = SlowReadChillerDriver(delay_s=0.05)
        group = _group(driver)
        try:
            await asyncio.gather(group.poll_device(), group.poll_device())

            assert driver.max_active_reads == 1
            assert driver.calls.count(("read_state",)) == 2
        finally:
            group.close()

    asyncio.run(scenario())


def test_chiller_putters_offload_blocking_hardware_calls():
    async def scenario():
        driver = SlowSetpointChillerDriver()
        group = _group(driver)
        try:
            _, tick_count = await asyncio.gather(
                group.SETPOINT_SET.write(value=21.0),
                _count_event_loop_ticks(0.10),
            )

            assert tick_count >= 3
            assert ("set_setpoint", 21.0) in driver.calls
        finally:
            group.close()

    asyncio.run(scenario())
