import pytest

from bdx_slow_control.config import ConfigurationError
from bdx_slow_control.drivers.hardware.cpx400dp import (
    CPX400DPDriver,
    build_cpx400dp_driver,
    parse_numeric_reply,
    parse_output_state_reply,
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


def test_parse_output_state_reply():
    assert parse_output_state_reply("OP1 1") is True
    assert parse_output_state_reply("0") is False
    with pytest.raises(ValueError, match="output state"):
        parse_output_state_reply("OP1 2")


def test_cpx400dp_read_channel_uses_real_hardware_queries():
    connection = FakeConnection(
        {
            "*IDN?": "THURLBY THANDAR,CPX400DP",
            "IFLOCK": "1",
            "V1O?": "5.000V",
            "I1O?": "0.125A",
            "V1?": "4.200V",
            "I1?": "0.500A",
            "OP1?": "1",
            "OVP1?": "6.000V",
            "OCP1?": "0.700A",
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
    assert state.voltage_setpoint == pytest.approx(4.2)
    assert state.current_limit == pytest.approx(0.5)
    assert state.output_enabled is True
    assert state.ovp == pytest.approx(6.0)
    assert state.ocp == pytest.approx(0.7)
    assert ("query", "V1O?") in connection.calls
    assert ("query", "I1O?") in connection.calls
    assert ("query", "V1?") in connection.calls
    assert ("query", "I1?") in connection.calls
    assert ("query", "OP1?") in connection.calls
    assert ("query", "OVP1?") in connection.calls
    assert ("query", "OCP1?") in connection.calls


def test_cpx400dp_setters_use_official_command_syntax():
    connection = FakeConnection(
        {
            "*IDN?": "THURLBY THANDAR,CPX400DP",
            "IFLOCK": "1",
        }
    )
    driver = CPX400DPDriver(
        connection=connection,
        channels=[1],
        configure_independent=True,
    )

    driver.set_voltage(1, 4.2)
    driver.set_current_limit(1, 0.3)
    driver.set_output(1, True)
    driver.set_ovp(1, 6.0)
    driver.set_ocp(1, 0.7)

    assert ("command", "CONFIG 2") in connection.calls
    assert ("command", "V1 4.2") in connection.calls
    assert ("command", "I1 0.3") in connection.calls
    assert ("command", "OP1 1") in connection.calls
    assert ("command", "OVP1 6") in connection.calls
    assert ("command", "OCP1 0.7") in connection.calls


def test_cpx400dp_all_outputs_off_queries_output_state():
    connection = FakeConnection(
        {
            "*IDN?": "THURLBY THANDAR,CPX400DP",
            "IFLOCK": "1",
            "OP1?": "0",
            "OP2?": "1",
        }
    )
    driver = CPX400DPDriver(connection=connection, channels=[1, 2])

    assert driver.all_outputs_off() is False
    assert ("query", "OP1?") in connection.calls
    assert ("query", "OP2?") in connection.calls

    driver.all_off()

    assert ("command", "OPALL 0") in connection.calls


def test_cpx400dp_rejects_values_outside_instrument_limits():
    driver = CPX400DPDriver(connection=FakeConnection(), channels=[1])

    with pytest.raises(ValueError, match="Voltage"):
        driver.set_voltage(1, 60.1)
    with pytest.raises(ValueError, match="Current limit"):
        driver.set_current_limit(1, 20.1)
    with pytest.raises(ValueError, match="OVP"):
        driver.set_ovp(1, 0.5)
    with pytest.raises(ValueError, match="OCP"):
        driver.set_ocp(1, 20.1)


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
