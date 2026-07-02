"""Environmental sensor IOC."""

from __future__ import annotations

from caproto import ChannelType
from caproto.server import pvproperty

from .common import ManagedIOC


class EnvironmentalSensorIOC(ManagedIOC):
    VALUE = pvproperty(value=0.0, dtype=float, read_only=True)
    UNIT = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    SENSOR_KIND = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    STATUS = pvproperty(value="STARTING", dtype=ChannelType.STRING, read_only=True)

    def __init__(self, *args, unit: str, sensor_kind: str, **kwargs) -> None:
        self.unit = unit
        self.sensor_kind = sensor_kind
        super().__init__(*args, **kwargs)

    async def poll_device(self) -> None:
        await self.UNIT.write(value=self.unit)
        await self.SENSOR_KIND.write(value=self.sensor_kind)
        await self.VALUE.write(value=self.driver.read_value())
        await self.STATUS.write(value="VALID")

    async def mark_failure(self, exc: Exception) -> None:
        await self.UNIT.write(value=self.unit)
        await self.SENSOR_KIND.write(value=self.sensor_kind)
        await self.STATUS.write(value="DISCONNECTED")
        await super().mark_failure(exc)
