"""Power-supply and high-voltage IOC groups."""

from __future__ import annotations

from dataclasses import dataclass

from caproto import ChannelType
from caproto.server import pvproperty

from .common import ManagedIOC


@dataclass(frozen=True)
class PowerChannelLimits:
    """Software limits applied before low-voltage PSU writes."""

    minimum_voltage: float = 0.0
    maximum_voltage: float = 60.0
    minimum_current_limit: float = 0.0
    maximum_current_limit: float = 20.0
    maximum_power: float = 420.0

    def validate(self, voltage: float, current_limit: float) -> None:
        if voltage < self.minimum_voltage or voltage > self.maximum_voltage:
            raise ValueError(
                "Requested voltage is outside the configured limits "
                f"({self.minimum_voltage:g} to {self.maximum_voltage:g} V)"
            )
        if (
            current_limit < self.minimum_current_limit
            or current_limit > self.maximum_current_limit
        ):
            raise ValueError(
                "Requested current limit is outside the configured limits "
                f"({self.minimum_current_limit:g} to {self.maximum_current_limit:g} A)"
            )
        if voltage * current_limit > self.maximum_power:
            raise ValueError(
                "Requested voltage-current product exceeds the configured limit "
                f"({self.maximum_power:g} W)"
            )


class PowerDeviceIOC(ManagedIOC):
    """Device-level power-supply commands and status."""

    ALLOFF_CMD = pvproperty(value=False, dtype=bool)
    ALL_OUTPUTS_OFF = pvproperty(value=True, dtype=bool, read_only=True)

    async def poll_device(self) -> None:
        await self.ALL_OUTPUTS_OFF.write(value=self.driver.all_outputs_off())

    @ALLOFF_CMD.putter
    async def ALLOFF_CMD(self, instance, value):
        if value:
            try:
                self.driver.all_off()
                await self.ALL_OUTPUTS_OFF.write(value=True)
                await self.ERROR_CODE.write(value=0)
                await self.ERROR_MESSAGE.write(value="")
            except Exception as exc:
                await self.mark_failure(exc)
                raise
        return False


class PowerChannelIOC(ManagedIOC):
    """Single-channel setpoint and readback group."""

    VOLTAGE_SET = pvproperty(value=0.0, dtype=float)
    VOLTAGE_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    CURRENT_LIMIT_SET = pvproperty(value=0.0, dtype=float)
    CURRENT_LIMIT_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    CURRENT_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    OUTPUT_SET = pvproperty(value=False, dtype=bool)
    OUTPUT_RBV = pvproperty(value=False, dtype=bool, read_only=True)
    OVP_SET = pvproperty(value=0.0, dtype=float)
    OVP_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    OCP_SET = pvproperty(value=0.0, dtype=float)
    OCP_RBV = pvproperty(value=0.0, dtype=float, read_only=True)

    def __init__(self, *args, channel: int, **kwargs) -> None:
        self.channel = int(channel)
        super().__init__(*args, **kwargs)

    async def poll_device(self) -> None:
        state = self.driver.read_channel(self.channel)
        await self.VOLTAGE_RBV.write(value=state.voltage)
        await self.CURRENT_LIMIT_RBV.write(value=state.current_limit)
        await self.CURRENT_RBV.write(value=state.current)
        await self.OUTPUT_RBV.write(value=state.output_enabled)
        await self.OVP_RBV.write(value=state.ovp)
        await self.OCP_RBV.write(value=state.ocp)

    @VOLTAGE_SET.putter
    async def VOLTAGE_SET(self, instance, value):
        try:
            self.driver.set_voltage(self.channel, float(value))
        except Exception as exc:
            await self.mark_failure(exc)
            raise
        return float(value)

    @CURRENT_LIMIT_SET.putter
    async def CURRENT_LIMIT_SET(self, instance, value):
        try:
            self.driver.set_current_limit(self.channel, float(value))
        except Exception as exc:
            await self.mark_failure(exc)
            raise
        return float(value)

    @OUTPUT_SET.putter
    async def OUTPUT_SET(self, instance, value):
        try:
            self.driver.set_output(self.channel, bool(value))
        except Exception as exc:
            await self.mark_failure(exc)
            raise
        return bool(value)

    @OVP_SET.putter
    async def OVP_SET(self, instance, value):
        try:
            self.driver.set_ovp(self.channel, float(value))
        except Exception as exc:
            await self.mark_failure(exc)
            raise
        return float(value)

    @OCP_SET.putter
    async def OCP_SET(self, instance, value):
        try:
            self.driver.set_ocp(self.channel, float(value))
        except Exception as exc:
            await self.mark_failure(exc)
            raise
        return float(value)


class LowVoltagePowerChannelIOC(PowerChannelIOC):
    """Low-voltage PSU channel with staged operator setpoints."""

    VOLTAGE_SET_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    OUTPUT_STATE = pvproperty(value="OFF", dtype=ChannelType.STRING, read_only=True)
    VOLTAGE_REQUEST = pvproperty(value=0.0, dtype=float)
    CURRENT_LIMIT_REQUEST = pvproperty(value=0.0, dtype=float)
    APPLY_CMD = pvproperty(value=False, dtype=bool)
    APPLY_STATUS = pvproperty(value="IDLE", dtype=ChannelType.STRING, read_only=True)
    APPLY_MESSAGE = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)

    def __init__(
        self,
        *args,
        limits: PowerChannelLimits | None = None,
        **kwargs,
    ) -> None:
        self.limits = limits or PowerChannelLimits()
        self._requests_initialized = False
        super().__init__(*args, **kwargs)

    async def poll_device(self) -> None:
        state = self.driver.read_channel(self.channel)
        await self.VOLTAGE_RBV.write(value=state.voltage)
        await self.VOLTAGE_SET_RBV.write(value=state.voltage_setpoint)
        await self.CURRENT_LIMIT_RBV.write(value=state.current_limit)
        await self.CURRENT_RBV.write(value=state.current)
        await self.OUTPUT_RBV.write(value=state.output_enabled)
        await self.OUTPUT_STATE.write(value="ON" if state.output_enabled else "OFF")
        await self.OVP_RBV.write(value=state.ovp)
        await self.OCP_RBV.write(value=state.ocp)
        if not self._requests_initialized:
            await self.VOLTAGE_REQUEST.write(value=state.voltage_setpoint)
            await self.CURRENT_LIMIT_REQUEST.write(value=state.current_limit)
            self._requests_initialized = True

    @APPLY_CMD.putter
    async def APPLY_CMD(self, instance, value):
        if not value:
            return False

        voltage = float(self.VOLTAGE_REQUEST.value)
        current_limit = float(self.CURRENT_LIMIT_REQUEST.value)
        try:
            self.limits.validate(voltage, current_limit)
        except ValueError as exc:
            await self.APPLY_STATUS.write(value="REJECTED")
            await self.APPLY_MESSAGE.write(value=str(exc))
            return False

        try:
            self.driver.set_voltage_and_current_limit(
                self.channel,
                voltage,
                current_limit,
            )
            state = self.driver.read_channel(self.channel)
        except Exception as exc:
            message = (
                f"Apply failed; hardware may have accepted only part of the request: {exc}"
            )
            await self.APPLY_STATUS.write(value="FAILED")
            await self.APPLY_MESSAGE.write(value=message)
            try:
                state = self.driver.read_channel(self.channel)
            except Exception:
                await self.mark_failure(exc)
                return False
            await self._write_readbacks(state)
            await self.mark_failure(exc)
            return False

        await self._write_readbacks(state)
        await self.APPLY_STATUS.write(value="APPLIED")
        await self.APPLY_MESSAGE.write(value="Request applied")
        await self.ERROR_CODE.write(value=0)
        await self.ERROR_MESSAGE.write(value="")
        return False

    async def _write_readbacks(self, state) -> None:
        await self.VOLTAGE_RBV.write(value=state.voltage)
        await self.VOLTAGE_SET_RBV.write(value=state.voltage_setpoint)
        await self.CURRENT_LIMIT_RBV.write(value=state.current_limit)
        await self.CURRENT_RBV.write(value=state.current)
        await self.OUTPUT_RBV.write(value=state.output_enabled)
        await self.OUTPUT_STATE.write(value="ON" if state.output_enabled else "OFF")
        await self.OVP_RBV.write(value=state.ovp)
        await self.OCP_RBV.write(value=state.ocp)
