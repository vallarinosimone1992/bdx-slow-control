"""Chiller IOC."""

from __future__ import annotations

import math

from caproto import ChannelType
from caproto.server import pvproperty

from .common import ManagedIOC


class ChillerIOC(ManagedIOC):
    TEMPERATURE_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    BATH_TEMPERATURE_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    CONTROLLED_TEMPERATURE_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    EXTERNAL_TEMPERATURE_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    SETPOINT_SET = pvproperty(value=20.0, dtype=float)
    SETPOINT_REQUEST = pvproperty(value=20.0, dtype=float)
    APPLY_SETPOINT_CMD = pvproperty(value=False, dtype=bool)
    APPLY_STATUS = pvproperty(value="IDLE", dtype=ChannelType.STRING, read_only=True)
    APPLY_MESSAGE = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    SETPOINT_RBV = pvproperty(value=20.0, dtype=float, read_only=True)
    PRESSURE_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    PRESSURE_ENABLED = pvproperty(value=False, dtype=bool, read_only=True)
    PRESSURE_VALID = pvproperty(value=False, dtype=bool, read_only=True)
    EXTERNAL_TEMPERATURE_ENABLED = pvproperty(value=False, dtype=bool, read_only=True)
    EXTERNAL_TEMPERATURE_VALID = pvproperty(value=False, dtype=bool, read_only=True)
    RUN_SET = pvproperty(value=False, dtype=bool)
    RUN_RBV = pvproperty(value=False, dtype=bool, read_only=True)
    RUN_STATE = pvproperty(value="STANDBY", dtype=ChannelType.STRING, read_only=True)
    FAULT = pvproperty(value=False, dtype=bool, read_only=True)
    PUMP_STAGE = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    COOLING_MODE = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    SAFE_MODE_STATUS = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    SAFE_SETPOINT_SET = pvproperty(value=20.0, dtype=float)
    SAFE_SETPOINT_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    COMM_TIMEOUT_SET = pvproperty(value=10.0, dtype=float)
    COMM_TIMEOUT_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    STANDBY_STATUS = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    DEVICE_STATUS = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    FAULT_DIAGNOSIS = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    TEMPERATURE_DEVIATION_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    DEVIATION_WARNING = pvproperty(value=False, dtype=bool, read_only=True)
    DEVIATION_ALARM = pvproperty(value=False, dtype=bool, read_only=True)
    DEVIATION_STATUS = pvproperty(value="UNKNOWN", dtype=ChannelType.STRING, read_only=True)

    def __init__(
        self,
        *args,
        minimum_setpoint_c: float = 5.0,
        maximum_setpoint_c: float = 40.0,
        warning_deviation_c: float = 0.2,
        alarm_deviation_c: float = 0.5,
        **kwargs,
    ) -> None:
        self.minimum_setpoint_c = float(minimum_setpoint_c)
        self.maximum_setpoint_c = float(maximum_setpoint_c)
        self.warning_deviation_c = float(warning_deviation_c)
        self.alarm_deviation_c = float(alarm_deviation_c)
        self._setpoint_request_initialized = False
        super().__init__(*args, **kwargs)

    async def poll_device(self) -> None:
        state = self.driver.read_state()
        await self._write_state(state)

    async def _write_state(self, state) -> None:
        await self.TEMPERATURE_RBV.write(value=state.temperature_c)
        await self.BATH_TEMPERATURE_RBV.write(value=state.bath_temperature_c)
        await self.CONTROLLED_TEMPERATURE_RBV.write(value=state.controlled_temperature_c)
        await self.EXTERNAL_TEMPERATURE_RBV.write(value=state.external_temperature_c)
        await self.SETPOINT_RBV.write(value=state.setpoint_c)
        await self.PRESSURE_RBV.write(value=state.pressure_bar)
        await self.PRESSURE_ENABLED.write(value=state.pressure_enabled)
        await self.PRESSURE_VALID.write(value=state.pressure_valid)
        await self.EXTERNAL_TEMPERATURE_ENABLED.write(value=state.external_temperature_enabled)
        await self.EXTERNAL_TEMPERATURE_VALID.write(value=state.external_temperature_valid)
        await self.RUN_RBV.write(value=state.running)
        await self.RUN_STATE.write(value="RUNNING" if state.running else "STANDBY")
        await self.FAULT.write(value=state.fault)
        await self.PUMP_STAGE.write(value=state.pump_stage)
        await self.COOLING_MODE.write(value=state.cooling_mode)
        await self.SAFE_MODE_STATUS.write(value=state.safe_mode_status)
        await self.SAFE_SETPOINT_RBV.write(value=state.safe_setpoint_c)
        await self.COMM_TIMEOUT_RBV.write(value=state.communication_timeout_s)
        await self.STANDBY_STATUS.write(value=state.standby_status)
        await self.DEVICE_STATUS.write(value=state.device_status)
        await self.FAULT_DIAGNOSIS.write(value=state.fault_diagnosis)
        await self._write_deviation(state.controlled_temperature_c, state.setpoint_c)
        if not self._setpoint_request_initialized:
            await self.SETPOINT_REQUEST.write(value=state.setpoint_c)
            self._setpoint_request_initialized = True

    async def _write_deviation(self, controlled_temperature_c: float, setpoint_c: float) -> None:
        if not math.isfinite(controlled_temperature_c) or not math.isfinite(setpoint_c):
            await self.TEMPERATURE_DEVIATION_RBV.write(value=math.nan)
            await self.DEVIATION_WARNING.write(value=False)
            await self.DEVIATION_ALARM.write(value=False)
            await self.DEVIATION_STATUS.write(value="UNKNOWN")
            return

        deviation = abs(controlled_temperature_c - setpoint_c)
        alarm = deviation >= self.alarm_deviation_c
        warning = deviation >= self.warning_deviation_c
        await self.TEMPERATURE_DEVIATION_RBV.write(value=deviation)
        await self.DEVIATION_WARNING.write(value=warning)
        await self.DEVIATION_ALARM.write(value=alarm)
        if alarm:
            status = "ALARM"
        elif warning:
            status = "WARNING"
        else:
            status = "OK"
        await self.DEVIATION_STATUS.write(value=status)

    @SETPOINT_SET.putter
    async def SETPOINT_SET(self, instance, value):
        value = float(value)
        self._validate_setpoint(value)
        try:
            self.driver.set_setpoint(value)
        except Exception as exc:
            await self.mark_failure(exc)
            raise
        return float(value)

    @APPLY_SETPOINT_CMD.putter
    async def APPLY_SETPOINT_CMD(self, instance, value):
        if not value:
            return False

        requested = float(self.SETPOINT_REQUEST.value)
        try:
            self._validate_setpoint(requested)
        except ValueError as exc:
            await self.APPLY_STATUS.write(value="REJECTED")
            await self.APPLY_MESSAGE.write(value=str(exc))
            return False

        try:
            self.driver.set_setpoint(requested)
            state = self.driver.read_state()
        except Exception as exc:
            await self.APPLY_STATUS.write(value="FAILED")
            await self.APPLY_MESSAGE.write(value=f"Setpoint apply failed: {exc}")
            await self.mark_failure(exc)
            return False

        await self._write_state(state)
        await self.APPLY_STATUS.write(value="APPLIED")
        await self.APPLY_MESSAGE.write(value="Setpoint request applied")
        await self.ERROR_CODE.write(value=0)
        await self.ERROR_MESSAGE.write(value="")
        return False

    @RUN_SET.putter
    async def RUN_SET(self, instance, value):
        try:
            self.driver.set_running(bool(value))
        except Exception as exc:
            await self.mark_failure(exc)
            raise
        return bool(value)

    @SAFE_SETPOINT_SET.putter
    async def SAFE_SETPOINT_SET(self, instance, value):
        try:
            self.driver.set_safe_setpoint(float(value))
        except Exception as exc:
            await self.mark_failure(exc)
            raise
        return float(value)

    @COMM_TIMEOUT_SET.putter
    async def COMM_TIMEOUT_SET(self, instance, value):
        try:
            self.driver.set_communication_timeout(float(value))
        except Exception as exc:
            await self.mark_failure(exc)
            raise
        return float(value)

    def _validate_setpoint(self, value: float) -> None:
        if value < self.minimum_setpoint_c or value > self.maximum_setpoint_c:
            raise ValueError(
                "Requested chiller setpoint is outside the configured limits "
                f"({self.minimum_setpoint_c:g} to {self.maximum_setpoint_c:g} degC)"
            )
