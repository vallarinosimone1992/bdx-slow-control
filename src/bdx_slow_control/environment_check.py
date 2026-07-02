"""Diagnostic checks for the BDX environment MCP9808 IOC."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Any, Callable, TextIO

from .config import ConfigurationError, load_json, require_list
from .drivers.hardware.mcp9808 import (
    LinuxI2CBus,
    MCP9808SensorDriver,
    parse_i2c_address,
    parse_resolution,
)


DeviceExists = Callable[[str], bool]
OpenDevice = Callable[[str, int], int]
CloseDevice = Callable[[int], None]
BusFactory = Callable[[str], Any]


@dataclass(frozen=True)
class MCP9808SensorConfig:
    """One MCP9808 sensor selected from the environment JSON configuration."""

    name: str
    bus: str
    address: int
    resolution_c: float


@dataclass(frozen=True)
class MCP9808CheckResult:
    """Result for one configured MCP9808 sensor."""

    sensor: MCP9808SensorConfig
    ok: bool
    temperature_c: float | None = None
    error: str = ""


def _sensor_name(raw: dict[str, Any], index: int) -> str:
    name = str(raw.get("name", "")).strip()
    if name:
        return name
    prefix = str(raw.get("prefix", "")).strip(":")
    if prefix:
        return prefix
    return f"sensor[{index}]"


def configured_mcp9808_sensors(config: dict[str, Any]) -> list[MCP9808SensorConfig]:
    """Return the MCP9808 sensors explicitly configured in an environment JSON object."""
    sensors = []
    for index, raw in enumerate(require_list(config, "sensors")):
        if not isinstance(raw, dict):
            raise ConfigurationError("Each sensor entry must be an object")

        mode = str(raw.get("mode", "simulation")).strip().lower()
        driver = str(raw.get("driver", "")).strip().lower()
        if mode != "hardware" or driver != "mcp9808":
            continue

        sensors.append(
            MCP9808SensorConfig(
                name=_sensor_name(raw, index),
                bus=str(raw.get("bus", "/dev/i2c-1")),
                address=parse_i2c_address(raw.get("address")),
                resolution_c=parse_resolution(raw.get("resolution_c", 0.0625)),
            )
        )

    if not sensors:
        raise ConfigurationError("No hardware MCP9808 sensors are configured")
    return sensors


def _default_device_exists(path: str) -> bool:
    return Path(path).exists()


def check_i2c_device_access(
    path: str,
    *,
    device_exists: DeviceExists = _default_device_exists,
    open_device: OpenDevice = os.open,
    close_device: CloseDevice = os.close,
) -> str | None:
    """Return an error string when the I2C device is missing or not read/write accessible."""
    if not device_exists(path):
        return f"I2C device does not exist: {path}"

    try:
        fd = open_device(path, os.O_RDWR)
    except PermissionError as exc:
        return f"Permission denied opening I2C device for read/write: {path}: {exc}"
    except OSError as exc:
        return f"Cannot open I2C device for read/write: {path}: {exc}"

    try:
        return None
    finally:
        close_device(fd)


def check_environment(
    config: dict[str, Any],
    *,
    device_exists: DeviceExists = _default_device_exists,
    open_device: OpenDevice = os.open,
    close_device: CloseDevice = os.close,
    bus_factory: BusFactory = LinuxI2CBus,
) -> list[MCP9808CheckResult]:
    """Check every configured MCP9808 sensor without scanning unconfigured addresses."""
    sensors = configured_mcp9808_sensors(config)

    bus_errors = {
        bus: error
        for bus in sorted({sensor.bus for sensor in sensors})
        if (
            error := check_i2c_device_access(
                bus,
                device_exists=device_exists,
                open_device=open_device,
                close_device=close_device,
            )
        )
    }

    results: list[MCP9808CheckResult] = []
    for sensor in sensors:
        if sensor.bus in bus_errors:
            results.append(MCP9808CheckResult(sensor=sensor, ok=False, error=bus_errors[sensor.bus]))
            continue

        bus = bus_factory(sensor.bus)
        driver = MCP9808SensorDriver(
            bus=bus,
            address=sensor.address,
            resolution_c=sensor.resolution_c,
        )
        try:
            temperature = driver.read_value()
        except OSError as exc:
            results.append(
                MCP9808CheckResult(
                    sensor=sensor,
                    ok=False,
                    error=(
                        f"Cannot read MCP9808 sensor {sensor.name} at "
                        f"0x{sensor.address:02X} on {sensor.bus}: {exc}"
                    ),
                )
            )
        else:
            results.append(
                MCP9808CheckResult(sensor=sensor, ok=True, temperature_c=temperature)
            )
        finally:
            close = getattr(bus, "close", None)
            if callable(close):
                close()

    return results


def format_result(result: MCP9808CheckResult) -> str:
    """Format one sensor diagnostic result for operator-facing output."""
    sensor = result.sensor
    base = f"sensor={sensor.name} bus={sensor.bus} address=0x{sensor.address:02X}"
    if result.ok:
        assert result.temperature_c is not None
        return f"{base} connectivity=OK temperature_c={result.temperature_c:.4f}"
    return f"{base} connectivity=ERROR error={result.error}"


def main(
    argv: list[str] | None = None,
    *,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
    device_exists: DeviceExists = _default_device_exists,
    open_device: OpenDevice = os.open,
    close_device: CloseDevice = os.close,
    bus_factory: BusFactory = LinuxI2CBus,
) -> int:
    """Run the MCP9808 environment diagnostic command."""
    parser = argparse.ArgumentParser(prog="bdx-environment-check")
    parser.add_argument(
        "--config",
        default="config/profiles/raspberry/environment.json",
        help="Environment IOC JSON configuration file",
    )
    args = parser.parse_args(argv)

    output = output or sys.stdout
    error_output = error_output or sys.stderr

    try:
        config = load_json(args.config)
        results = check_environment(
            config,
            device_exists=device_exists,
            open_device=open_device,
            close_device=close_device,
            bus_factory=bus_factory,
        )
    except (ConfigurationError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=error_output)
        return 2

    for result in results:
        print(format_result(result), file=output)

    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
