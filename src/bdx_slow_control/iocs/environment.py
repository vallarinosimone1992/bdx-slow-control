"""Environmental sensor IOC."""

from __future__ import annotations

from caproto import ChannelType
from caproto.server import PVGroup, pvproperty

from .common import ManagedIOC
from ..runtime import RuntimeSettings
from ..util import utc_timestamp


class EnvironmentSummaryIOC(PVGroup):
    """Environment-wide health PVs for the operator display."""

    HEARTBEAT = pvproperty(value=0, dtype=int, read_only=True)
    LAST_TEMPERATURE_UPDATE = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)

    def __init__(
        self,
        *args,
        runtime_settings: RuntimeSettings,
        **kwargs,
    ) -> None:
        self.runtime_settings = runtime_settings
        super().__init__(*args, **kwargs)

    async def record_temperature_update(self) -> None:
        await self.LAST_TEMPERATURE_UPDATE.write(value=utc_timestamp())

    @HEARTBEAT.startup
    async def HEARTBEAT(self, instance, async_lib):
        counter = 0
        while True:
            counter = (counter + 1) % 2_147_483_647
            await instance.write(value=counter)
            await async_lib.library.sleep(self.runtime_settings.update_period)


class EnvironmentalSensorIOC(ManagedIOC):
    VALUE = pvproperty(value=0.0, dtype=float, read_only=True)
    UNIT = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    SENSOR_KIND = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    STATUS = pvproperty(value="STARTING", dtype=ChannelType.STRING, read_only=True)
    STATUS_OK = pvproperty(value=0, dtype=int, read_only=True)

    def __init__(
        self,
        *args,
        unit: str,
        sensor_kind: str,
        summary: EnvironmentSummaryIOC | None = None,
        **kwargs,
    ) -> None:
        self.unit = unit
        self.sensor_kind = sensor_kind
        self.summary = summary
        super().__init__(*args, **kwargs)

    async def poll_device(self) -> None:
        await self.UNIT.write(value=self.unit)
        await self.SENSOR_KIND.write(value=self.sensor_kind)
        await self.VALUE.write(value=self.driver.read_value())
        await self.STATUS.write(value="VALID")
        await self.STATUS_OK.write(value=1)
        if self.summary is not None and self.sensor_kind == "temperature":
            await self.summary.record_temperature_update()

    async def mark_failure(self, exc: Exception) -> None:
        await self.UNIT.write(value=self.unit)
        await self.SENSOR_KIND.write(value=self.sensor_kind)
        await self.STATUS.write(value="DISCONNECTED")
        await self.STATUS_OK.write(value=0)
        await super().mark_failure(exc)
