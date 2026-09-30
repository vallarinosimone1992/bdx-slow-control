"""Environmental sensor IOC."""

from __future__ import annotations

from collections import deque
import math
import time

from caproto import ChannelType
from caproto.server import PVGroup, pvproperty

from .common import ManagedIOC
from ..runtime import RuntimeSettings
from ..util import utc_timestamp


class EnvironmentSummaryIOC(PVGroup):
    """Environment-wide health PVs for the operator display."""

    HEARTBEAT = pvproperty(value=0, dtype=int, read_only=True)
    LAST_TEMPERATURE_UPDATE = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    TEMPERATURE_MIN = pvproperty(value=math.nan, dtype=float, read_only=True, precision=2)
    TEMPERATURE_MAX = pvproperty(value=math.nan, dtype=float, read_only=True, precision=2)
    TEMPERATURE_SPREAD = pvproperty(value=math.nan, dtype=float, read_only=True, precision=2)

    def __init__(
        self,
        *args,
        runtime_settings: RuntimeSettings,
        **kwargs,
    ) -> None:
        self.runtime_settings = runtime_settings
        self._temperature_values: dict[str, tuple[float, float]] = {}
        super().__init__(*args, **kwargs)

    async def record_temperature_update(self, sensor_name: str, value: float) -> None:
        now = time.monotonic()
        self._temperature_values[sensor_name] = (now, float(value))
        max_age = max(10.0, 3.0 * float(self.runtime_settings.update_period))
        active = [
            sample
            for timestamp, sample in self._temperature_values.values()
            if now - timestamp <= max_age
        ]
        if active:
            minimum = min(active)
            maximum = max(active)
            await self.TEMPERATURE_MIN.write(value=minimum)
            await self.TEMPERATURE_MAX.write(value=maximum)
            await self.TEMPERATURE_SPREAD.write(value=maximum - minimum)
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
        sensor_name: str = "",
        summary: EnvironmentSummaryIOC | None = None,
        **kwargs,
    ) -> None:
        self.unit = unit
        self.sensor_kind = sensor_kind
        self.sensor_name = sensor_name
        self.summary = summary
        super().__init__(*args, **kwargs)

    async def poll_device(self) -> None:
        await self.UNIT.write(value=self.unit)
        await self.SENSOR_KIND.write(value=self.sensor_kind)
        await self.VALUE.write(value=self.driver.read_value())
        await self.STATUS.write(value="VALID")
        await self.STATUS_OK.write(value=1)
        if self.summary is not None and self.sensor_kind == "temperature":
            await self.summary.record_temperature_update(
                self.sensor_name or "temperature",
                float(self.VALUE.value),
            )

    async def mark_failure(self, exc: Exception) -> None:
        await self.UNIT.write(value=self.unit)
        await self.SENSOR_KIND.write(value=self.sensor_kind)
        await self.STATUS.write(value="DISCONNECTED")
        await self.STATUS_OK.write(value=0)
        await super().mark_failure(exc)


class TemperatureSensorIOC(EnvironmentalSensorIOC):
    """Temperature sensor with rolling diagnostic change metrics."""

    CHANGE_10M = pvproperty(value=0.0, dtype=float, read_only=True, precision=2)
    CHANGE_1H = pvproperty(value=0.0, dtype=float, read_only=True, precision=2)

    def __init__(self, *args, **kwargs) -> None:
        self._temperature_history = deque()
        super().__init__(*args, **kwargs)

    async def poll_device(self) -> None:
        value = float(self.driver.read_value())
        now = time.monotonic()
        self._temperature_history.append((now, value))
        cutoff = now - 3600.0
        while self._temperature_history and self._temperature_history[0][0] < cutoff:
            self._temperature_history.popleft()

        ten_minutes = [
            sample for timestamp, sample in self._temperature_history
            if timestamp >= now - 600.0
        ]
        one_hour = [sample for _, sample in self._temperature_history]
        change_10m = max(ten_minutes) - min(ten_minutes) if ten_minutes else 0.0
        change_1h = max(one_hour) - min(one_hour) if one_hour else 0.0

        await self.UNIT.write(value=self.unit)
        await self.SENSOR_KIND.write(value=self.sensor_kind)
        await self.VALUE.write(value=value)
        await self.CHANGE_10M.write(value=change_10m)
        await self.CHANGE_1H.write(value=change_1h)
        await self.STATUS.write(value="VALID")
        await self.STATUS_OK.write(value=1)
        if self.summary is not None:
            await self.summary.record_temperature_update(
                self.sensor_name or "temperature",
                value,
            )
