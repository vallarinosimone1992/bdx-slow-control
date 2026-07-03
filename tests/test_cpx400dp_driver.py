import pytest

from bdx_slow_control.config import ConfigurationError
from bdx_slow_control.drivers.hardware.cpx400dp import (
    CPX400DPDriver,
    build_cpx400dp_driver,
    parse_numeric_reply,
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


def test_parse_numeric_reply_uses_last_number():
    assert parse_numeric_reply("5.000V") == pytest.approx(5.0)
    assert parse_numeric_reply("I 1 20.00") == pytest.approx(20.0)
    assert parse_numeric_reply("0,125A") == pytest.approx(0.125)


def test_cpx400dp_read_channel_and_setters():
    connection = FakeConnection(
        {
            "*IDN?": "THURLBY THANDAR,CPX400DP",
            "IFLOCK": "1",
            "V1O?": "5.000V",
            "I1O?": "0.125A",
            "I1?": "0.500A",
        }
    )
    driver = CPX400DPDriver(
        connection=connection,
        channels=[1],
        configure_independent=True,
    )

    state = driver.read_channel(1)
    assert state.voltage == pytest.approx(5.0)
    assert state.current == pytest.approx(0.125)
    assert state.current_limit == pytest.approx(0.5)
    assert state.output_enabled is False

    driver.set_voltage(1, 4.2)
    driver.set_current_limit(1, 0.3)
    driver.set_output(1, True)
    driver.set_ovp(1, 6.0)
    driver.set_ocp(1, 0.7)

    state = driver.read_channel(1)
    assert state.output_enabled is True
    assert state.ovp == pytest.approx(6.0)
    assert state.ocp == pytest.approx(0.7)
    assert ("command", "CONFIG 2") in connection.calls
    assert ("command", "V1 4.2") in connection.calls
    assert ("command", "I1 0.3") in connection.calls
    assert ("command", "OP1 1") in connection.calls


def test_cpx400dp_all_off_clears_cached_outputs():
    connection = FakeConnection(
        {
            "*IDN?": "THURLBY THANDAR,CPX400DP",
            "IFLOCK": "1",
        }
    )
    driver = CPX400DPDriver(connection=connection, channels=[1, 2])

    driver.set_output(1, True)
    driver.set_output(2, True)
    assert driver.all_outputs_off() is False

    driver.all_off()

    assert driver.all_outputs_off() is True
    assert ("command", "OPALL 0") in connection.calls


def test_cpx400dp_ping_reports_connection_failure():
    connection = FakeConnection({"*IDN?": OSError("network unreachable")})
    driver = CPX400DPDriver(connection=connection, channels=[1])

    assert driver.ping() is False
    assert connection.close_count == 2
    assert isinstance(driver.last_error, OSError)


def test_build_cpx400dp_driver_rejects_missing_host():
    with pytest.raises(ConfigurationError, match="requires host"):
        build_cpx400dp_driver({"channels": [1]})


def test_cpx400dp_rejects_unsupported_channel():
    with pytest.raises(ConfigurationError, match="supports only channels 1 and 2"):
        CPX400DPDriver(connection=FakeConnection(), channels=[3])
