"""Common caproto IOC behavior."""

from __future__ import annotations

import logging

from caproto import ChannelType
from caproto.server import PVGroup, pvproperty

from ..runtime import RuntimeSettings
from ..util import utc_timestamp

logger = logging.getLogger(__name__)


class ManagedIOC(PVGroup):
    """Base group with health, heartbeat, and error PVs."""

    HEARTBEAT = pvproperty(value=0, dtype=int, read_only=True)
    IOC_STATE = pvproperty(value="STARTING", dtype=ChannelType.STRING, read_only=True)
    COMM_STATUS = pvproperty(value="STARTING", dtype=ChannelType.STRING, read_only=True)
    COMM_OK = pvproperty(value=False, dtype=bool, read_only=True)
    LAST_UPDATE = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    ERROR_CODE = pvproperty(value=0, dtype=int, read_only=True)
    ERROR_MESSAGE = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    SIMULATION = pvproperty(value=False, dtype=bool, read_only=True)
    CLEAR_ERROR_CMD = pvproperty(value=False, dtype=bool)

    def __init__(
        self,
        *args,
        driver,
        runtime_settings: RuntimeSettings | None = None,
        poll_interval: float = 1.0,
        **kwargs,
    ) -> None:
        self.driver = driver
        self.runtime_settings = runtime_settings or RuntimeSettings(
            initial_update_period=float(poll_interval),
        )
        self._poll_failed = False
        self._last_failure_message = ""
        super().__init__(*args, **kwargs)

    async def poll_device(self) -> None:
        """Poll the device and update subsystem-specific PVs."""
        raise NotImplementedError

    @property
    def poll_period(self) -> float:
        """Return the effective polling period for this IOC group."""
        return self.runtime_settings.update_period

    async def check_driver_communication(self) -> None:
        """Check driver communication before polling the device."""
        if not self.driver.ping():
            last_error = getattr(self.driver, "last_error", None)
            if last_error is not None:
                raise ConnectionError(
                    f"Driver communication check failed: {last_error}"
                ) from last_error
            raise ConnectionError("Driver communication check failed")

    async def mark_success(self) -> None:
        status = "SIMULATION" if bool(getattr(self.driver, "simulation", False)) else "OK"
        await self.COMM_STATUS.write(value=status)
        await self.COMM_OK.write(value=True)
        await self.LAST_UPDATE.write(value=utc_timestamp())
        await self.ERROR_CODE.write(value=0)
        await self.ERROR_MESSAGE.write(value="")
        if self._poll_failed:
            logger.info("IOC communication recovered for prefix %s", self.prefix)
        self._poll_failed = False
        self._last_failure_message = ""

    async def mark_failure(self, exc: Exception) -> None:
        message = str(exc) or exc.__class__.__name__
        await self.COMM_STATUS.write(value="DEVICE_ERROR")
        await self.COMM_OK.write(value=False)
        await self.ERROR_CODE.write(value=1)
        await self.ERROR_MESSAGE.write(value=message)
        should_log = not self._poll_failed or message != self._last_failure_message
        self._poll_failed = True
        self._last_failure_message = message
        if should_log:
            logger.warning("IOC poll failed for prefix %s: %s", self.prefix, message)
            logger.debug(
                "IOC poll failure traceback for prefix %s",
                self.prefix,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    @HEARTBEAT.startup
    async def HEARTBEAT(self, instance, async_lib):
        await self.SIMULATION.write(value=bool(getattr(self.driver, "simulation", False)))
        await self.IOC_STATE.write(value="RUNNING")
        counter = 0
        while True:
            counter = (counter + 1) % 2_147_483_647
            await instance.write(value=counter)
            try:
                await self.check_driver_communication()
                await self.poll_device()
                await self.mark_success()
            except Exception as exc:
                await self.mark_failure(exc)
            await async_lib.library.sleep(self.poll_period)

    @CLEAR_ERROR_CMD.putter
    async def CLEAR_ERROR_CMD(self, instance, value):
        if value:
            await self.ERROR_CODE.write(value=0)
            await self.ERROR_MESSAGE.write(value="")
        return False
