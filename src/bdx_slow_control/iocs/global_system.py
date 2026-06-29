"""Global slow-control state, update timing, and interlock commands."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from caproto import ChannelType
from caproto.server import PVGroup, pvproperty

from ..runtime import RuntimeSettings
from ..util import utc_timestamp


class GlobalIOC(PVGroup):
    HEARTBEAT = pvproperty(value=0, dtype=int, read_only=True)
    SYSTEM_STATE = pvproperty(value="STANDBY", dtype=ChannelType.STRING, read_only=True)
    READY = pvproperty(value=False, dtype=bool, read_only=True)
    INTERLOCK_ACTIVE = pvproperty(value=False, dtype=bool, read_only=True)
    INTERLOCK_REASON = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    INTERLOCK_TEST_CMD = pvproperty(value=False, dtype=bool)
    INTERLOCK_RESET_CMD = pvproperty(value=False, dtype=bool)
    ALLOFF_CMD = pvproperty(value=False, dtype=bool)
    LAST_ACTION = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    SIMULATION = pvproperty(value=True, dtype=bool, read_only=True)
    UPDATE_PERIOD_SET = pvproperty(value=5.0, dtype=float)
    UPDATE_PERIOD_RBV = pvproperty(value=5.0, dtype=float, read_only=True)
    UPDATE_FREQUENCY_RBV = pvproperty(value=0.2, dtype=float, read_only=True)
    MIN_UPDATE_PERIOD_RBV = pvproperty(value=2.0, dtype=float, read_only=True)
    MAX_UPDATE_PERIOD_RBV = pvproperty(value=3600.0, dtype=float, read_only=True)

    def __init__(
        self,
        *args,
        runtime_settings: RuntimeSettings,
        initial_state: str = "STANDBY",
        all_off_callbacks: Sequence[Callable[[], None]] = (),
        **kwargs,
    ) -> None:
        self.runtime_settings = runtime_settings
        self.initial_state = initial_state
        self.all_off_callbacks = tuple(all_off_callbacks)
        super().__init__(*args, **kwargs)

    async def _write_timing_readbacks(self) -> None:
        await self.UPDATE_PERIOD_RBV.write(value=self.runtime_settings.update_period)
        await self.UPDATE_FREQUENCY_RBV.write(value=self.runtime_settings.update_frequency)
        await self.MIN_UPDATE_PERIOD_RBV.write(
            value=self.runtime_settings.minimum_update_period
        )
        await self.MAX_UPDATE_PERIOD_RBV.write(
            value=self.runtime_settings.maximum_update_period
        )

    def _all_off(self) -> None:
        for callback in self.all_off_callbacks:
            callback()

    @HEARTBEAT.startup
    async def HEARTBEAT(self, instance, async_lib):
        await self.SYSTEM_STATE.write(value=self.initial_state)
        await self.READY.write(value=True)
        await self.UPDATE_PERIOD_SET.write(value=self.runtime_settings.update_period)
        await self._write_timing_readbacks()
        counter = 0
        while True:
            counter = (counter + 1) % 2_147_483_647
            await instance.write(value=counter)
            await self._write_timing_readbacks()
            await async_lib.library.sleep(self.runtime_settings.update_period)

    @UPDATE_PERIOD_SET.putter
    async def UPDATE_PERIOD_SET(self, instance, value):
        period = self.runtime_settings.set_update_period(float(value))
        await self._write_timing_readbacks()
        await self.LAST_ACTION.write(
            value=f"{utc_timestamp()} update period set to {period:g} s"
        )
        return period

    @INTERLOCK_TEST_CMD.putter
    async def INTERLOCK_TEST_CMD(self, instance, value):
        if value:
            self._all_off()
            await self.INTERLOCK_ACTIVE.write(value=True)
            await self.INTERLOCK_REASON.write(value="Manual simulation interlock test")
            await self.SYSTEM_STATE.write(value="INTERLOCK")
            await self.READY.write(value=False)
            await self.LAST_ACTION.write(
                value=f"{utc_timestamp()} simulation interlock triggered"
            )
        return False

    @INTERLOCK_RESET_CMD.putter
    async def INTERLOCK_RESET_CMD(self, instance, value):
        if value:
            await self.INTERLOCK_ACTIVE.write(value=False)
            await self.INTERLOCK_REASON.write(value="")
            await self.SYSTEM_STATE.write(value="STANDBY")
            await self.READY.write(value=True)
            await self.LAST_ACTION.write(
                value=f"{utc_timestamp()} interlock reset requested"
            )
        return False

    @ALLOFF_CMD.putter
    async def ALLOFF_CMD(self, instance, value):
        if value:
            self._all_off()
            await self.SYSTEM_STATE.write(value="SAFE")
            await self.READY.write(value=False)
            await self.LAST_ACTION.write(
                value=f"{utc_timestamp()} global all-off requested"
            )
        return False
