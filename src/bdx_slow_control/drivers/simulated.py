"""Simulation drivers used by the prototype."""

from __future__ import annotations

import math
import time

from .base import (
    ChillerDriver,
    ChillerState,
    DaqCrateDriver,
    DaqCrateState,
    HighVoltageDriver,
    PowerChannelState,
    PowerSupplyDriver,
    SensorDriver,
)


class SimulatedPowerSupplyDriver(PowerSupplyDriver):
    simulation = True

    def __init__(
        self,
        channels: list[int],
        initial_voltage: float = 0.0,
        initial_current_limit: float = 0.5,
        initial_ovp: float = 10.0,
        initial_ocp: float = 1.0,
    ) -> None:
        self._connected = True
        self._channels = {
            channel: {
                "voltage": float(initial_voltage),
                "current_limit": float(initial_current_limit),
                "output_enabled": False,
                "ovp": float(initial_ovp),
                "ocp": float(initial_ocp),
            }
            for channel in channels
        }

    def ping(self) -> bool:
        return self._connected

    def _state(self, channel: int) -> dict[str, float | bool]:
        try:
            return self._channels[channel]
        except KeyError as exc:
            raise ValueError(f"Unknown power-supply channel: {channel}") from exc

    def read_channel(self, channel: int) -> PowerChannelState:
        state = self._state(channel)
        output_enabled = bool(state["output_enabled"])
        voltage = float(state["voltage"]) if output_enabled else 0.0
        current_limit = float(state["current_limit"])
        simulated_load = min(current_limit, abs(voltage) * 0.01) if output_enabled else 0.0
        return PowerChannelState(
            voltage=voltage,
            current=simulated_load,
            current_limit=current_limit,
            output_enabled=output_enabled,
            ovp=float(state["ovp"]),
            ocp=float(state["ocp"]),
        )

    def set_voltage(self, channel: int, value: float) -> None:
        if value < 0:
            raise ValueError("Voltage must be non-negative")
        state = self._state(channel)
        if value > float(state["ovp"]):
            raise ValueError("Requested voltage exceeds OVP")
        state["voltage"] = float(value)

    def set_current_limit(self, channel: int, value: float) -> None:
        if value < 0:
            raise ValueError("Current limit must be non-negative")
        self._state(channel)["current_limit"] = float(value)

    def set_output(self, channel: int, enabled: bool) -> None:
        self._state(channel)["output_enabled"] = bool(enabled)

    def set_ovp(self, channel: int, value: float) -> None:
        if value <= 0:
            raise ValueError("OVP must be positive")
        self._state(channel)["ovp"] = float(value)

    def set_ocp(self, channel: int, value: float) -> None:
        if value <= 0:
            raise ValueError("OCP must be positive")
        self._state(channel)["ocp"] = float(value)

    def all_off(self) -> None:
        for state in self._channels.values():
            state["output_enabled"] = False

    def all_outputs_off(self) -> bool:
        return all(not bool(state["output_enabled"]) for state in self._channels.values())


class SimulatedHighVoltageDriver(SimulatedPowerSupplyDriver, HighVoltageDriver):
    """Simulation backend for a multi-channel HV supply."""


class SimulatedChillerDriver(ChillerDriver):
    simulation = True

    def __init__(
        self,
        initial_setpoint_c: float,
        initial_temperature_c: float,
        initial_pressure_bar: float,
    ) -> None:
        self._connected = True
        self._setpoint = float(initial_setpoint_c)
        self._temperature = float(initial_temperature_c)
        self._pressure = float(initial_pressure_bar)
        self._running = False
        self._fault = False

    def ping(self) -> bool:
        return self._connected

    def read_state(self) -> ChillerState:
        target = self._setpoint if self._running else 23.0
        self._temperature += (target - self._temperature) * 0.05
        return ChillerState(
            temperature_c=self._temperature,
            setpoint_c=self._setpoint,
            pressure_bar=self._pressure if self._running else 0.0,
            running=self._running,
            fault=self._fault,
        )

    def set_setpoint(self, value_c: float) -> None:
        if not 0.0 <= value_c <= 40.0:
            raise ValueError("Chiller setpoint is outside the simulated range")
        self._setpoint = float(value_c)

    def set_running(self, running: bool) -> None:
        self._running = bool(running)


class SimulatedSensorDriver(SensorDriver):
    simulation = True

    def __init__(self, initial_value: float, amplitude: float = 0.0) -> None:
        self._connected = True
        self._initial = float(initial_value)
        self._amplitude = float(amplitude)
        self._start = time.monotonic()

    def ping(self) -> bool:
        return self._connected

    def read_value(self) -> float:
        elapsed = time.monotonic() - self._start
        return self._initial + self._amplitude * math.sin(elapsed / 30.0)


class SimulatedDaqCrateDriver(DaqCrateDriver):
    simulation = True

    def __init__(self, initial_configuration: str) -> None:
        self._connected = True
        self._state = "STANDBY"
        self._configuration = initial_configuration
        self._ready = True
        self._error = ""

    def ping(self) -> bool:
        return self._connected

    def read_state(self) -> DaqCrateState:
        return DaqCrateState(
            state=self._state,
            configuration_applied=self._configuration,
            ready=self._ready,
            error=self._error,
        )

    def apply_configuration(self, name: str) -> None:
        if not name.strip():
            raise ValueError("Configuration name must not be empty")
        self._configuration = name.strip()
        self._ready = True
        self._error = ""

    def set_state(self, state: str) -> None:
        allowed = {"OFF", "STANDBY", "CONFIGURED", "RUNNING"}
        normalized = state.strip().upper()
        if normalized not in allowed:
            raise ValueError(f"Unsupported DAQ state: {state}")
        self._state = normalized
