import asyncio

import pytest

from bdx_slow_control.drivers.base import PowerChannelState
from bdx_slow_control.builders import build_psu
from bdx_slow_control.config import load_json
from bdx_slow_control.iocs.power import LowVoltagePowerChannelIOC, PowerChannelLimits
from bdx_slow_control.runtime import RuntimeSettings
from pathlib import Path


class RecordingPowerDriver:
    simulation = False

    def __init__(self):
        self.calls = []
        self.state = PowerChannelState(
            voltage=0.0,
            current=0.0,
            current_limit=0.5,
            output_enabled=False,
            voltage_setpoint=0.0,
            ovp=10.0,
            ocp=1.0,
        )

    def ping(self):
        return True

    def read_channel(self, channel):
        self.calls.append(("read_channel", channel))
        return self.state

    def set_voltage(self, channel, value):
        self.calls.append(("set_voltage", channel, value))
        self.state = PowerChannelState(
            voltage=self.state.voltage,
            current=self.state.current,
            current_limit=self.state.current_limit,
            output_enabled=self.state.output_enabled,
            voltage_setpoint=float(value),
            ovp=self.state.ovp,
            ocp=self.state.ocp,
        )

    def set_current_limit(self, channel, value):
        self.calls.append(("set_current_limit", channel, value))
        self.state = PowerChannelState(
            voltage=self.state.voltage,
            current=self.state.current,
            current_limit=float(value),
            output_enabled=self.state.output_enabled,
            voltage_setpoint=self.state.voltage_setpoint,
            ovp=self.state.ovp,
            ocp=self.state.ocp,
        )

    def set_voltage_and_current_limit(self, channel, voltage, current_limit):
        self.calls.append(("apply", channel, voltage, current_limit))
        self.set_current_limit(channel, current_limit)
        self.set_voltage(channel, voltage)

    def set_output(self, channel, enabled):
        self.calls.append(("set_output", channel, enabled))

    def set_ovp(self, channel, value):
        self.calls.append(("set_ovp", channel, value))

    def set_ocp(self, channel, value):
        self.calls.append(("set_ocp", channel, value))

    def all_off(self):
        self.calls.append(("all_off",))

    def all_outputs_off(self):
        return True


def _group(driver=None, limits=None):
    return LowVoltagePowerChannelIOC(
        prefix="BDX:PSU:LV1:CH1:",
        driver=driver or RecordingPowerDriver(),
        channel=1,
        limits=limits or PowerChannelLimits(),
        runtime_settings=RuntimeSettings(),
    )


def test_default_voltage_current_and_power_limits():
    limits = PowerChannelLimits()

    limits.validate(60.0, 7.0)
    with pytest.raises(ValueError, match="voltage"):
        limits.validate(60.1, 1.0)
    with pytest.raises(ValueError, match="current limit"):
        limits.validate(1.0, 20.1)
    with pytest.raises(ValueError, match="voltage-current product"):
        limits.validate(60.0, 8.0)


def test_configurable_lower_bdx_limits():
    limits = PowerChannelLimits(maximum_voltage=5.0, maximum_current_limit=1.0)

    limits.validate(5.0, 1.0)
    with pytest.raises(ValueError, match="voltage"):
        limits.validate(5.1, 1.0)
    with pytest.raises(ValueError, match="current limit"):
        limits.validate(5.0, 1.1)


def test_staged_values_do_not_change_hardware_before_apply():
    async def scenario():
        driver = RecordingPowerDriver()
        group = _group(driver)

        await group.VOLTAGE_REQUEST.write(value=5.0)
        await group.CURRENT_LIMIT_REQUEST.write(value=0.25)

        assert driver.calls == []

    asyncio.run(scenario())


def test_invalid_apply_performs_no_writes():
    async def scenario():
        driver = RecordingPowerDriver()
        group = _group(driver)
        await group.VOLTAGE_REQUEST.write(value=60.0)
        await group.CURRENT_LIMIT_REQUEST.write(value=8.0)

        await group.APPLY_CMD.write(value=True)

        assert not any(call[0] in {"apply", "set_voltage", "set_current_limit"} for call in driver.calls)
        assert group.APPLY_STATUS.value == "REJECTED"
        assert "voltage-current product" in group.APPLY_MESSAGE.value

    asyncio.run(scenario())


def test_successful_apply_performs_both_writes_and_updates_readbacks():
    async def scenario():
        driver = RecordingPowerDriver()
        group = _group(driver)
        await group.VOLTAGE_REQUEST.write(value=12.0)
        await group.CURRENT_LIMIT_REQUEST.write(value=1.5)

        await group.APPLY_CMD.write(value=True)

        assert ("apply", 1, 12.0, 1.5) in driver.calls
        assert ("set_current_limit", 1, 1.5) in driver.calls
        assert ("set_voltage", 1, 12.0) in driver.calls
        assert group.VOLTAGE_SET_RBV.value == pytest.approx(12.0)
        assert group.CURRENT_LIMIT_RBV.value == pytest.approx(1.5)
        assert group.APPLY_STATUS.value == "APPLIED"

    asyncio.run(scenario())


def test_low_voltage_psu_float_pvs_advertise_three_decimal_precision():
    pvdb, _ = build_psu(load_json(Path("config/profiles/main-server/psu.json")))

    float_suffixes = (
        "VOLTAGE_SET",
        "VOLTAGE_RBV",
        "VOLTAGE_SET_RBV",
        "VOLTAGE_REQUEST",
        "CURRENT_LIMIT_SET",
        "CURRENT_LIMIT_RBV",
        "CURRENT_LIMIT_REQUEST",
        "CURRENT_RBV",
        "OVP_SET",
        "OVP_RBV",
        "OCP_SET",
        "OCP_RBV",
    )
    for device in ("LV1", "LV2"):
        for channel in ("CH1", "CH2"):
            prefix = f"BDX:PSU:{device}:{channel}:"
            for suffix in float_suffixes:
                assert pvdb[f"{prefix}{suffix}"].precision == 3
