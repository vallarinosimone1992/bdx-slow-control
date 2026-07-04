"""Driver construction from JSON configuration."""

from __future__ import annotations

from typing import Any

from ..config import ConfigurationError
from .simulated import (
    SimulatedChillerDriver,
    SimulatedDaqCrateDriver,
    SimulatedHighVoltageDriver,
    SimulatedPowerSupplyDriver,
    SimulatedSensorDriver,
)


def _mode(config: dict[str, Any]) -> str:
    value = str(config.get("mode", "simulation")).strip().lower()
    if value not in {"simulation", "hardware"}:
        raise ConfigurationError(f"Unsupported driver mode: {value}")
    return value


def build_psu_driver(config: dict[str, Any]):
    if _mode(config) == "hardware":
        driver = str(config.get("driver", "")).strip().lower()
        if driver in {"cpx400dp", "tti_cpx400dp"}:
            from .hardware.cpx400dp import build_cpx400dp_driver

            return build_cpx400dp_driver(config)
        raise NotImplementedError("Hardware PSU driver requires driver='cpx400dp'")

    channels = [int(value) for value in config.get("channels", [])]
    if not channels:
        raise ConfigurationError("PSU configuration requires at least one channel")
    return SimulatedPowerSupplyDriver(
        channels=channels,
        initial_voltage=float(config.get("initial_voltage", 0.0)),
        initial_current_limit=float(config.get("initial_current_limit", 0.5)),
        initial_ovp=float(config.get("initial_ovp", 10.0)),
        initial_ocp=float(config.get("initial_ocp", 1.0)),
    )


def build_hv_driver(config: dict[str, Any]):
    if _mode(config) == "hardware":
        driver = str(config.get("driver", "")).strip().lower()
        if driver == "genh600":
            from .hardware.genh600 import build_genh600_driver

            return build_genh600_driver(config)
        raise NotImplementedError("Hardware HV driver requires driver='genh600'")

    channels = [int(value) for value in config.get("channels", [])]
    if not channels:
        raise ConfigurationError("HV configuration requires at least one channel")
    return SimulatedHighVoltageDriver(
        channels=channels,
        initial_voltage=float(config.get("initial_voltage", 0.0)),
        initial_current_limit=float(config.get("initial_current_limit", 0.001)),
        initial_ovp=float(config.get("initial_ovp", 2500.0)),
        initial_ocp=float(config.get("initial_ocp", 0.01)),
    )


def build_chiller_driver(config: dict[str, Any]):
    if _mode(config) == "hardware":
        driver = str(config.get("driver", "")).strip().lower()
        if driver in {"ecosilver_re_1225s", "lauda_ecosilver_re_1225s", "lauda"}:
            from .hardware.ecosilver_re_1225s import build_ecosilver_re_1225s_driver

            return build_ecosilver_re_1225s_driver(config)
        raise NotImplementedError(
            "Hardware chiller driver requires driver='ecosilver_re_1225s'"
        )

    return SimulatedChillerDriver(
        initial_setpoint_c=float(config.get("initial_setpoint_c", 20.0)),
        initial_temperature_c=float(config.get("initial_temperature_c", 21.0)),
        initial_pressure_bar=float(config.get("initial_pressure_bar", 0.5)),
        pressure_enabled=bool(config.get("pressure_enabled", False)),
        external_temperature_enabled=bool(config.get("external_temperature_enabled", False)),
        minimum_setpoint_c=float(config.get("minimum_setpoint_c", 5.0)),
        maximum_setpoint_c=float(config.get("maximum_setpoint_c", 40.0)),
    )


def build_sensor_driver(config: dict[str, Any]):
    if _mode(config) == "hardware":
        driver = str(config.get("driver", "")).strip().lower()
        if driver == "mcp9808":
            from .hardware.mcp9808 import build_mcp9808_driver

            return build_mcp9808_driver(config)
        raise NotImplementedError("Hardware sensor driver requires driver='mcp9808'")
    return SimulatedSensorDriver(
        initial_value=float(config.get("initial_value", 0.0)),
        amplitude=float(config.get("amplitude", 0.0)),
    )


def build_daq_driver(config: dict[str, Any]) -> SimulatedDaqCrateDriver:
    if _mode(config) != "simulation":
        raise NotImplementedError("Hardware DAQ crate driver is not implemented")
    return SimulatedDaqCrateDriver(
        initial_configuration=str(config.get("initial_configuration", "default"))
    )
