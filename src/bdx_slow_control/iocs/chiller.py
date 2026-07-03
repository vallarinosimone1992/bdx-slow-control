"""Chiller IOC."""

from __future__ import annotations

from caproto import ChannelType
from caproto.server import pvproperty

from .common import ManagedIOC


class ChillerIOC(ManagedIOC):
    TEMPERATURE_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    BATH_TEMPERATURE_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    CONTROLLED_TEMPERATURE_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    EXTERNAL_TEMPERATURE_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    SETPOINT_SET = pvproperty(value=20.0, dtype=float)
    SETPOINT_RBV = pvproperty(value=20.0, dtype=float, read_only=True)
    PRESSURE_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    RUN_SET = pvproperty(value=False, dtype=bool)
    RUN_RBV = pvproperty(value=False, dtype=bool, read_only=True)
    FAULT = pvproperty(value=False, dtype=bool, read_only=True)
    PUMP_STAGE = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    COOLING_MODE = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    SAFE_MODE_STATUS = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    STANDBY_STATUS = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    DEVICE_STATUS = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    FAULT_DIAGNOSIS = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)

    async def poll_device(self) -> None:
        state = self.driver.read_state()
        await self.TEMPERATURE_RBV.write(value=state.temperature_c)
        await self.BATH_TEMPERATURE_RBV.write(value=state.bath_temperature_c)
        await self.CONTROLLED_TEMPERATURE_RBV.write(value=state.controlled_temperature_c)
        await self.EXTERNAL_TEMPERATURE_RBV.write(value=state.external_temperature_c)
        await self.SETPOINT_RBV.write(value=state.setpoint_c)
        await self.PRESSURE_RBV.write(value=state.pressure_bar)
        await self.RUN_RBV.write(value=state.running)
        await self.FAULT.write(value=state.fault)
        await self.PUMP_STAGE.write(value=state.pump_stage)
        await self.COOLING_MODE.write(value=state.cooling_mode)
        await self.SAFE_MODE_STATUS.write(value=state.safe_mode_status)
        await self.STANDBY_STATUS.write(value=state.standby_status)
        await self.DEVICE_STATUS.write(value=state.device_status)
        await self.FAULT_DIAGNOSIS.write(value=state.fault_diagnosis)

    @SETPOINT_SET.putter
    async def SETPOINT_SET(self, instance, value):
        self.driver.set_setpoint(float(value))
        return float(value)

    @RUN_SET.putter
    async def RUN_SET(self, instance, value):
        self.driver.set_running(bool(value))
        return bool(value)
