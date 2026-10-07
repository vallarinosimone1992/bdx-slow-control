"""Global slow-control state, update timing, interlock, and notifier controls."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
import time
from zoneinfo import ZoneInfo

from caproto import ChannelType
from caproto.server import PVGroup, pvproperty

from ..runtime import RuntimeSettings
from ..util import utc_timestamp


NOTIFIER_HEARTBEAT_TIMEOUT_SECONDS = 15.0
NOTIFIER_TIMEZONE = ZoneInfo("Europe/Rome")


def _pv_boolean(value) -> bool:
    """Convert EPICS boolean-enum values without treating 'Off' as true."""
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="strict")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"on", "true", "yes", "1"}:
            return True
        if normalized in {"off", "false", "no", "0"}:
            return False
        raise ValueError(f"Unsupported boolean PV value: {value!r}")
    return bool(value)


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

    NOTIFIER_ENABLED = pvproperty(value=True, dtype=bool, read_only=True)
    NOTIFIER_ONLINE = pvproperty(value=False, dtype=bool, read_only=True)
    NOTIFIER_STATUS = pvproperty(value="OFFLINE", dtype=ChannelType.STRING, read_only=True)
    NOTIFIER_HEARTBEAT = pvproperty(value=0, dtype=int)
    NOTIFIER_SNOOZE_MINUTES = pvproperty(value=60.0, dtype=float)
    NOTIFIER_SNOOZE_CMD = pvproperty(value=False, dtype=bool)
    NOTIFIER_RESUME_CMD = pvproperty(value=False, dtype=bool)
    NOTIFIER_SNOOZE_UNTIL = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    NOTIFIER_LAST_HEARTBEAT = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)

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
        self._notifier_last_heartbeat_monotonic: float | None = None
        self._notifier_snooze_until_epoch: float | None = None
        super().__init__(*args, **kwargs)

    async def _write_timing_readbacks(self) -> None:
        await self.UPDATE_PERIOD_RBV.write(value=self.runtime_settings.update_period)
        await self.UPDATE_FREQUENCY_RBV.write(value=self.runtime_settings.update_frequency)
        await self.MIN_UPDATE_PERIOD_RBV.write(value=self.runtime_settings.minimum_update_period)
        await self.MAX_UPDATE_PERIOD_RBV.write(value=self.runtime_settings.maximum_update_period)

    async def _update_notifier_status(self) -> None:
        now_epoch = time.time()
        now_monotonic = time.monotonic()
        if self._notifier_snooze_until_epoch is not None and now_epoch >= self._notifier_snooze_until_epoch:
            self._notifier_snooze_until_epoch = None
            await self.NOTIFIER_ENABLED.write(value=True)
            await self.NOTIFIER_SNOOZE_UNTIL.write(value="")

        online = (
            self._notifier_last_heartbeat_monotonic is not None
            and now_monotonic - self._notifier_last_heartbeat_monotonic <= NOTIFIER_HEARTBEAT_TIMEOUT_SECONDS
        )
        enabled = _pv_boolean(self.NOTIFIER_ENABLED.value)
        await self.NOTIFIER_ONLINE.write(value=online)
        if not online:
            status = "OFFLINE"
        elif enabled:
            status = "ACTIVE"
        else:
            status = "SNOOZED"
        await self.NOTIFIER_STATUS.write(value=status)

    def _all_off(self) -> None:
        for callback in self.all_off_callbacks:
            callback()

    @HEARTBEAT.startup
    async def HEARTBEAT(self, instance, async_lib):
        await self.SYSTEM_STATE.write(value=self.initial_state)
        await self.READY.write(value=True)
        await self.UPDATE_PERIOD_SET.write(value=self.runtime_settings.update_period)
        await self._write_timing_readbacks()
        await self._update_notifier_status()
        counter = 0
        while True:
            counter = (counter + 1) % 2_147_483_647
            await instance.write(value=counter)
            await self._write_timing_readbacks()
            await self._update_notifier_status()
            await async_lib.library.sleep(self.runtime_settings.update_period)

    @NOTIFIER_HEARTBEAT.putter
    async def NOTIFIER_HEARTBEAT(self, instance, value):
        self._notifier_last_heartbeat_monotonic = time.monotonic()
        await self.NOTIFIER_LAST_HEARTBEAT.write(value=utc_timestamp())
        await self._update_notifier_status()
        return int(value)

    @NOTIFIER_SNOOZE_MINUTES.putter
    async def NOTIFIER_SNOOZE_MINUTES(self, instance, value):
        minutes = float(value)
        if minutes <= 0:
            raise ValueError("Notifier snooze duration must be positive")
        return minutes

    @NOTIFIER_SNOOZE_CMD.putter
    async def NOTIFIER_SNOOZE_CMD(self, instance, value):
        if _pv_boolean(value):
            minutes = float(self.NOTIFIER_SNOOZE_MINUTES.value)
            self._notifier_snooze_until_epoch = time.time() + 60.0 * minutes
            local_until = datetime.fromtimestamp(
                self._notifier_snooze_until_epoch, tz=NOTIFIER_TIMEZONE
            ).strftime("%Y-%m-%d %H:%M:%S")
            await self.NOTIFIER_ENABLED.write(value=False)
            await self.NOTIFIER_SNOOZE_UNTIL.write(value=local_until)
            await self.LAST_ACTION.write(
                value=f"{utc_timestamp()} notifier snoozed for {minutes:g} min"
            )
            await self._update_notifier_status()
        return False

    @NOTIFIER_RESUME_CMD.putter
    async def NOTIFIER_RESUME_CMD(self, instance, value):
        if _pv_boolean(value):
            self._notifier_snooze_until_epoch = None
            await self.NOTIFIER_ENABLED.write(value=True)
            await self.NOTIFIER_SNOOZE_UNTIL.write(value="")
            await self.LAST_ACTION.write(value=f"{utc_timestamp()} notifier resumed")
            await self._update_notifier_status()
        return False

    @UPDATE_PERIOD_SET.putter
    async def UPDATE_PERIOD_SET(self, instance, value):
        period = self.runtime_settings.set_update_period(float(value))
        await self._write_timing_readbacks()
        await self.LAST_ACTION.write(value=f"{utc_timestamp()} update period set to {period:g} s")
        return period

    @INTERLOCK_TEST_CMD.putter
    async def INTERLOCK_TEST_CMD(self, instance, value):
        if value:
            self._all_off()
            await self.INTERLOCK_ACTIVE.write(value=True)
            await self.INTERLOCK_REASON.write(value="Manual simulation interlock test")
            await self.SYSTEM_STATE.write(value="INTERLOCK")
            await self.READY.write(value=False)
            await self.LAST_ACTION.write(value=f"{utc_timestamp()} simulation interlock triggered")
        return False

    @INTERLOCK_RESET_CMD.putter
    async def INTERLOCK_RESET_CMD(self, instance, value):
        if value:
            await self.INTERLOCK_ACTIVE.write(value=False)
            await self.INTERLOCK_REASON.write(value="")
            await self.SYSTEM_STATE.write(value="STANDBY")
            await self.READY.write(value=True)
            await self.LAST_ACTION.write(value=f"{utc_timestamp()} interlock reset requested")
        return False

    @ALLOFF_CMD.putter
    async def ALLOFF_CMD(self, instance, value):
        if value:
            self._all_off()
            await self.SYSTEM_STATE.write(value="SAFE")
            await self.READY.write(value=False)
            await self.LAST_ACTION.write(value=f"{utc_timestamp()} global all-off requested")
        return False
