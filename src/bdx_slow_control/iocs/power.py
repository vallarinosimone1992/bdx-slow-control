"""Power-supply and high-voltage IOC groups."""

from __future__ import annotations

from caproto.server import pvproperty

from .common import ManagedIOC


class PowerDeviceIOC(ManagedIOC):
    """Device-level power-supply commands and status."""

    ALLOFF_CMD = pvproperty(value=False, dtype=bool)
    ALL_OUTPUTS_OFF = pvproperty(value=True, dtype=bool, read_only=True)

    async def poll_device(self) -> None:
        await self.ALL_OUTPUTS_OFF.write(value=self.driver.all_outputs_off())

    @ALLOFF_CMD.putter
    async def ALLOFF_CMD(self, instance, value):
        if value:
            self.driver.all_off()
            await self.ALL_OUTPUTS_OFF.write(value=True)
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
        self.driver.set_voltage(self.channel, float(value))
        return float(value)

    @CURRENT_LIMIT_SET.putter
    async def CURRENT_LIMIT_SET(self, instance, value):
        self.driver.set_current_limit(self.channel, float(value))
        return float(value)

    @OUTPUT_SET.putter
    async def OUTPUT_SET(self, instance, value):
        self.driver.set_output(self.channel, bool(value))
        return bool(value)

    @OVP_SET.putter
    async def OVP_SET(self, instance, value):
        self.driver.set_ovp(self.channel, float(value))
        return float(value)

    @OCP_SET.putter
    async def OCP_SET(self, instance, value):
        self.driver.set_ocp(self.channel, float(value))
        return float(value)
