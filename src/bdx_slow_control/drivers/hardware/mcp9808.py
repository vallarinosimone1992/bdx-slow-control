"""MCP9808 temperature sensor driver using Linux i2c-dev."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import os
from typing import Any

from ...config import ConfigurationError
from ..base import SensorDriver

I2C_SLAVE = 0x0703

TEMPERATURE_REGISTER = 0x05
RESOLUTION_REGISTER = 0x08
RESOLUTION_CODES = {
    0.5: 0x00,
    0.25: 0x01,
    0.125: 0x02,
    0.0625: 0x03,
}


def parse_i2c_address(value: Any) -> int:
    """Parse and validate a 7-bit I2C address."""
    if isinstance(value, int):
        address = value
    elif isinstance(value, str):
        try:
            address = int(value, 0)
        except ValueError as exc:
            raise ConfigurationError(f"Invalid I2C address: {value!r}") from exc
    else:
        raise ConfigurationError("I2C address must be an integer or string")

    if not 0x03 <= address <= 0x77:
        raise ConfigurationError(f"I2C address out of 7-bit range: {address!r}")
    return address


def parse_resolution(value: Any) -> float:
    """Parse an MCP9808 temperature resolution in degC."""
    try:
        resolution = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid MCP9808 resolution: {value!r}") from exc

    for supported in RESOLUTION_CODES:
        if abs(resolution - supported) < 1e-9:
            return supported
    supported_values = ", ".join(str(item) for item in sorted(RESOLUTION_CODES))
    raise ConfigurationError(f"Unsupported MCP9808 resolution {resolution}; use {supported_values}")


class LinuxI2CBus:
    """Small wrapper around Linux /dev/i2c-* using only the standard library."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._fd: int | None = None

    def open(self) -> None:
        if self._fd is None:
            self._fd = os.open(self.path, os.O_RDWR)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _select_slave(self, address: int) -> None:
        self.open()
        assert self._fd is not None
        fcntl.ioctl(self._fd, I2C_SLAVE, address)

    def write(self, address: int, payload: bytes) -> None:
        self._select_slave(address)
        assert self._fd is not None
        written = os.write(self._fd, payload)
        if written != len(payload):
            raise OSError(f"Short I2C write to 0x{address:02X}: {written}/{len(payload)} bytes")

    def read(self, address: int, length: int) -> bytes:
        self._select_slave(address)
        assert self._fd is not None
        data = os.read(self._fd, length)
        if len(data) != length:
            raise OSError(f"Short I2C read from 0x{address:02X}: {len(data)}/{length} bytes")
        return data

    def write_register(self, address: int, register: int, *values: int) -> None:
        self.write(address, bytes([register, *values]))

    def read_register(self, address: int, register: int, length: int) -> bytes:
        self.write(address, bytes([register]))
        return self.read(address, length)


@dataclass
class MCP9808SensorDriver(SensorDriver):
    """Read one MCP9808 sensor."""

    bus: LinuxI2CBus
    address: int
    resolution_c: float = 0.0625

    simulation = False

    def __post_init__(self) -> None:
        self._initialized = False

    def initialize(self) -> None:
        code = RESOLUTION_CODES[self.resolution_c]
        self.bus.write_register(self.address, RESOLUTION_REGISTER, code)
        self._initialized = True

    def ping(self) -> bool:
        try:
            if not self._initialized:
                self.initialize()
            return True
        except OSError:
            self._initialized = False
            return False

    def read_value(self) -> float:
        try:
            if not self._initialized:
                self.initialize()
            data = self.bus.read_register(self.address, TEMPERATURE_REGISTER, 2)
        except OSError:
            self._initialized = False
            raise
        return decode_temperature(data)


def decode_temperature(data: bytes) -> float:
    """Decode two MCP9808 ambient temperature bytes into degC."""
    if len(data) != 2:
        raise ValueError(f"MCP9808 temperature read requires 2 bytes, got {len(data)}")
    raw = ((data[0] & 0x1F) << 8) | data[1]
    if raw & 0x1000:
        raw -= 0x2000
    return raw * 0.0625


_BUSES: dict[str, LinuxI2CBus] = {}


def shared_bus(path: str) -> LinuxI2CBus:
    """Return a shared bus object for sensors on the same Linux I2C device."""
    return _BUSES.setdefault(path, LinuxI2CBus(path))


def build_mcp9808_driver(config: dict[str, Any]) -> MCP9808SensorDriver:
    """Build an MCP9808 driver from one environment sensor JSON object."""
    bus_path = str(config.get("bus", "/dev/i2c-1"))
    address = parse_i2c_address(config.get("address"))
    resolution_c = parse_resolution(config.get("resolution_c", 0.0625))
    return MCP9808SensorDriver(
        bus=shared_bus(bus_path),
        address=address,
        resolution_c=resolution_c,
    )
