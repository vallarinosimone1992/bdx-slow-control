"""DAQ crate interface IOC."""

from __future__ import annotations

from caproto import ChannelType
from caproto.server import pvproperty

from .common import ManagedIOC
from ..util import utc_timestamp


class DaqCrateIOC(ManagedIOC):
    STATE_SET = pvproperty(value="STANDBY", dtype=ChannelType.STRING)
    STATE_RBV = pvproperty(value="STANDBY", dtype=ChannelType.STRING, read_only=True)
    CONFIG_REQUEST = pvproperty(value="", dtype=ChannelType.STRING)
    CONFIG_APPLIED = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    READY = pvproperty(value=False, dtype=bool, read_only=True)
    ERROR = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    MEASUREMENT_ACTIVE = pvproperty(value=False, dtype=bool, read_only=True)
    RUN_ID_SET = pvproperty(value="", dtype=ChannelType.STRING)
    RUN_ID_RBV = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    EXPECTED_STATE_SET = pvproperty(value="STANDBY", dtype=ChannelType.STRING)
    EXPECTED_STATE_RBV = pvproperty(
        value="STANDBY",
        dtype=ChannelType.STRING,
        read_only=True,
    )
    RUN_START_TIME = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    RUN_END_TIME = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)

    def __init__(self, *args, **kwargs) -> None:
        self._measurement_was_active = False
        super().__init__(*args, **kwargs)

    async def poll_device(self) -> None:
        state = self.driver.read_state()
        await self.STATE_RBV.write(value=state.state)
        await self.CONFIG_APPLIED.write(value=state.configuration_applied)
        await self.READY.write(value=state.ready)
        await self.ERROR.write(value=state.error)
        await self._write_measurement_state(state.state)

    async def _write_measurement_state(self, state: str) -> None:
        active = str(state).strip().upper() == "RUNNING"
        await self.MEASUREMENT_ACTIVE.write(value=active)
        if active and not self._measurement_was_active:
            await self.RUN_START_TIME.write(value=utc_timestamp())
            await self.RUN_END_TIME.write(value="")
        elif self._measurement_was_active and not active:
            await self.RUN_END_TIME.write(value=utc_timestamp())
        self._measurement_was_active = active

    @STATE_SET.putter
    async def STATE_SET(self, instance, value):
        text = str(value)
        self.driver.set_state(text)
        state = self.driver.read_state()
        await self.STATE_RBV.write(value=state.state)
        await self._write_measurement_state(state.state)
        return text

    @CONFIG_REQUEST.putter
    async def CONFIG_REQUEST(self, instance, value):
        text = str(value)
        self.driver.apply_configuration(text)
        return text

    @RUN_ID_SET.putter
    async def RUN_ID_SET(self, instance, value):
        text = str(value).strip()
        await self.RUN_ID_RBV.write(value=text)
        return text

    @EXPECTED_STATE_SET.putter
    async def EXPECTED_STATE_SET(self, instance, value):
        text = str(value).strip().upper()
        if text not in {"OFF", "STANDBY", "CONFIGURED", "RUNNING"}:
            raise ValueError(f"Unsupported expected DAQ state: {value}")
        await self.EXPECTED_STATE_RBV.write(value=text)
        return text
