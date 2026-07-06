"""Shared runtime settings for the aggregated prototype IOC."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock


@dataclass
class RuntimeSettings:
    """Thread-safe mutable IOC update-period settings."""

    initial_update_period: float = 1.0
    minimum_update_period: float = 1.0
    maximum_update_period: float = 3600.0
    _update_period: float = field(init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._update_period = self._validated(self.initial_update_period)

    def _validated(self, value: float) -> float:
        period = float(value)
        if not self.minimum_update_period <= period <= self.maximum_update_period:
            raise ValueError(
                f"Update period must be between {self.minimum_update_period:g} and "
                f"{self.maximum_update_period:g} seconds"
            )
        return period

    @property
    def update_period(self) -> float:
        with self._lock:
            return self._update_period

    @property
    def update_frequency(self) -> float:
        return 1.0 / self.update_period

    def set_update_period(self, value: float) -> float:
        period = self._validated(value)
        with self._lock:
            self._update_period = period
        return period
