"""Chiller IOC."""

from __future__ import annotations

from caproto.server import pvproperty

from .common import ManagedIOC


class ChillerIOC(ManagedIOC):
    TEMPERATURE_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    SETPOINT_SET = pvproperty(value=20.0, dtype=float)
    SETPOINT_RBV = pvproperty(value=20.0, dtype=float, read_only=True)
    PRESSURE_RBV = pvproperty(value=0.0, dtype=float, read_only=True)
    RUN_SET = pvproperty(value=False, dtype=bool)
    RUN_RBV = pvproperty(value=False, dtype=bool, read_only=True)
    FAULT = pvproperty(value=False, dtype=bool, read_only=True)

    async def poll_device(self) -> None:
        state = self.driver.read_state()
        await self.TEMPERATURE_RBV.write(value=state.temperature_c)
        await self.SETPOINT_RBV.write(value=state.setpoint_c)
        await self.PRESSURE_RBV.write(value=state.pressure_bar)
        await self.RUN_RBV.write(value=state.running)
        await self.FAULT.write(value=state.fault)

    @SETPOINT_SET.putter
    async def SETPOINT_SET(self, instance, value):
        self.driver.set_setpoint(float(value))
        return float(value)

    @RUN_SET.putter
    async def RUN_SET(self, instance, value):
        self.driver.set_running(bool(value))
        return bool(value)
