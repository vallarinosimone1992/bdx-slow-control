import pytest

from bdx_slow_control.config import ConfigurationError
from bdx_slow_control.drivers.hardware.genh600 import (
    GENH600Driver,
    build_genh600_driver,
    parse_float_reply,
)


class FakeConnection:
    def __init__(self, replies=None):
        self.replies = replies or {}
        self.calls = []
        self.close_count = 0

    def command(self, command):
        self.calls.append(("command", command))

    def query(self, command):
        self.calls.append(("query", command))
        reply = self.replies.get(command)
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            raise AssertionError(f"Unexpected query: {command}")
        return reply

    def close(self):
        self.close_count += 1


def test_parse_float_reply_accepts_decimal_comma():
    assert parse_float_reply("120.500") == pytest.approx(120.5)
    assert parse_float_reply("0,001") == pytest.approx(0.001)


def test_genh600_read_channel_and_setters():
    connection = FakeConnection(
        {
            "IDN?": "TDK-LAMBDA,GENH600",
            "MV?": "120.000",
            "MC?": "0.125",
        }
    )
    driver = GENH600Driver(connection=connection, channels=[1], address=6)

    state = driver.read_channel(1)
    assert state.voltage == pytest.approx(120.0)
    assert state.current == pytest.approx(0.125)
    assert state.current_limit == pytest.approx(0.001)
    assert state.output_enabled is False

    driver.set_voltage(1, 100.0)
    driver.set_current_limit(1, 0.5)
    driver.set_output(1, True)
    driver.set_ovp(1, 650.0)
    driver.set_ocp(1, 0.7)

    state = driver.read_channel(1)
    assert state.output_enabled is True
    assert state.ovp == pytest.approx(650.0)
    assert state.ocp == pytest.approx(0.7)
    assert ("command", "ADR 6") in connection.calls
    assert ("command", "CLS") in connection.calls
    assert ("command", "RMT 1") in connection.calls
    assert ("command", "PV 100") in connection.calls
    assert ("command", "PC 0.5") in connection.calls
    assert ("command", "OUT 1") in connection.calls


def test_genh600_all_off_clears_cached_output():
    connection = FakeConnection({"IDN?": "TDK-LAMBDA,GENH600"})
    driver = GENH600Driver(connection=connection, channels=[1])

    driver.set_output(1, True)
    assert driver.all_outputs_off() is False

    driver.all_off()

    assert driver.all_outputs_off() is True
    assert ("command", "OUT 0") in connection.calls


def test_genh600_ping_reports_connection_failure():
    connection = FakeConnection({"IDN?": OSError("serial disconnected")})
    driver = GENH600Driver(connection=connection, channels=[1])

    assert driver.ping() is False
    assert connection.close_count == 2
    assert isinstance(driver.last_error, OSError)


def test_build_genh600_driver_rejects_missing_port():
    with pytest.raises(ConfigurationError, match="requires port"):
        build_genh600_driver({"channels": [1]})


def test_genh600_rejects_unsupported_channel():
    with pytest.raises(ConfigurationError, match="supports only channel 1"):
        GENH600Driver(connection=FakeConnection(), channels=[2])
