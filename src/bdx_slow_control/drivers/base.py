"""Hardware-independent driver interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class PowerChannelState:
    voltage: float
    current: float
    current_limit: float
    output_enabled: bool
    ovp: float = 0.0
    ocp: float = 0.0


@dataclass(frozen=True)
class ChillerState:
    temperature_c: float
    setpoint_c: float
    pressure_bar: float
    running: bool
    fault: bool
    bath_temperature_c: float = 0.0
    controlled_temperature_c: float = 0.0
    external_temperature_c: float = 0.0
    pump_stage: str = ""
    cooling_mode: str = ""
    safe_mode_status: str = ""
    standby_status: str = ""
    device_status: str = ""
    fault_diagnosis: str = ""


@dataclass(frozen=True)
class DaqCrateState:
    state: str
    configuration_applied: str
    ready: bool
    error: str


class BaseDriver(ABC):
    """Common driver behavior."""

    simulation: bool = False

    @abstractmethod
    def ping(self) -> bool:
        """Return whether communication is available."""


class PowerSupplyDriver(BaseDriver, ABC):
    @abstractmethod
    def read_channel(self, channel: int) -> PowerChannelState:
        pass

    @abstractmethod
    def set_voltage(self, channel: int, value: float) -> None:
        pass

    @abstractmethod
    def set_current_limit(self, channel: int, value: float) -> None:
        pass

    @abstractmethod
    def set_output(self, channel: int, enabled: bool) -> None:
        pass

    @abstractmethod
    def set_ovp(self, channel: int, value: float) -> None:
        pass

    @abstractmethod
    def set_ocp(self, channel: int, value: float) -> None:
        pass

    @abstractmethod
    def all_off(self) -> None:
        pass

    @abstractmethod
    def all_outputs_off(self) -> bool:
        pass


class ChillerDriver(BaseDriver, ABC):
    @abstractmethod
    def read_state(self) -> ChillerState:
        pass

    @abstractmethod
    def set_setpoint(self, value_c: float) -> None:
        pass

    @abstractmethod
    def set_running(self, running: bool) -> None:
        pass


class SensorDriver(BaseDriver, ABC):
    @abstractmethod
    def read_value(self) -> float:
        pass


class HighVoltageDriver(PowerSupplyDriver, ABC):
    """High-voltage driver interface."""


class DaqCrateDriver(BaseDriver, ABC):
    @abstractmethod
    def read_state(self) -> DaqCrateState:
        pass

    @abstractmethod
    def apply_configuration(self, name: str) -> None:
        pass

    @abstractmethod
    def set_state(self, state: str) -> None:
        pass
