"""DAQ crate interface IOC."""

from __future__ import annotations

from caproto import ChannelType
from caproto.server import pvproperty

from .common import ManagedIOC


class DaqCrateIOC(ManagedIOC):
    STATE_SET = pvproperty(value="STANDBY", dtype=ChannelType.STRING)
    STATE_RBV = pvproperty(value="STANDBY", dtype=ChannelType.STRING, read_only=True)
    CONFIG_REQUEST = pvproperty(value="", dtype=ChannelType.STRING)
    CONFIG_APPLIED = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    READY = pvproperty(value=False, dtype=bool, read_only=True)
    ERROR = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)

    async def poll_device(self) -> None:
        state = self.driver.read_state()
        await self.STATE_RBV.write(value=state.state)
        await self.CONFIG_APPLIED.write(value=state.configuration_applied)
        await self.READY.write(value=state.ready)
        await self.ERROR.write(value=state.error)

    @STATE_SET.putter
    async def STATE_SET(self, instance, value):
        text = str(value)
        self.driver.set_state(text)
        return text

    @CONFIG_REQUEST.putter
    async def CONFIG_REQUEST(self, instance, value):
        text = str(value)
        self.driver.apply_configuration(text)
        return text
