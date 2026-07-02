import pytest

from bdx_slow_control.builders import build_environment
from bdx_slow_control.config import load_json
from bdx_slow_control.config import ConfigurationError
from bdx_slow_control.drivers.hardware.mcp9808 import (
    MCP9808SensorDriver,
    RESOLUTION_REGISTER,
    decode_temperature,
    parse_i2c_address,
    parse_resolution,
)


class FakeBus:
    def __init__(self, data: bytes):
        self.data = data
        self.writes = []

    def write_register(self, address, register, *values):
        self.writes.append((address, register, values))

    def read_register(self, address, register, length):
        assert length == 2
        return self.data


def test_decode_temperature_positive():
    assert decode_temperature(bytes([0x01, 0x90])) == pytest.approx(25.0)


def test_decode_temperature_negative():
    assert decode_temperature(bytes([0x1F, 0xC0])) == pytest.approx(-4.0)


def test_mcp9808_driver_initializes_resolution_and_reads_value():
    bus = FakeBus(bytes([0x01, 0x91]))
    driver = MCP9808SensorDriver(bus=bus, address=0x18, resolution_c=0.0625)

    assert driver.ping() is True
    assert bus.writes == [(0x18, RESOLUTION_REGISTER, (0x03,))]
    assert driver.read_value() == pytest.approx(25.0625)


def test_parse_i2c_address_accepts_hex_string():
    assert parse_i2c_address("0x1B") == 0x1B


def test_parse_i2c_address_rejects_invalid_value():
    with pytest.raises(ConfigurationError):
        parse_i2c_address("not-an-address")


def test_parse_resolution_rejects_unsupported_value():
    with pytest.raises(ConfigurationError):
        parse_resolution(0.1)


def test_raspberry_environment_config_builds_pvdb_without_opening_i2c():
    pvdb, _ = build_environment(load_json("config/profiles/raspberry/environment.json"))
    assert "BDX:ENV:TEMP:T00:VALUE" in pvdb
    assert "BDX:ENV:TEMP:T03:VALUE" in pvdb


def test_raspberry_environment_config_uses_expected_bus_and_addresses():
    config = load_json("config/profiles/raspberry/environment.json")
    sensors = config["sensors"]
    assert config["server"]["poll_interval"] == 5.0
    assert {sensor["bus"] for sensor in sensors} == {"/dev/i2c-1"}
    assert [sensor["address"] for sensor in sensors] == ["0x18", "0x19", "0x1A", "0x1B"]
