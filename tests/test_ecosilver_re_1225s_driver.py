import pytest

from bdx_slow_control.config import ConfigurationError
from bdx_slow_control.drivers.hardware.ecosilver_re_1225s import (
    ECOSilverRE1225SDriver,
    build_ecosilver_re_1225s_driver,
    fault_reply_to_bool,
    parse_float_reply,
    standby_reply_to_running,
)


class FakeConnection:
    def __init__(self, replies=None):
        self.replies = replies or {}
        self.calls = []

    def query(self, command):
        self.calls.append(("query", command))
        reply = self.replies.get(command)
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            raise AssertionError(f"Unexpected query: {command}")
        return reply

    def command(self, command, *, require_ok=False):
        self.calls.append(("command", command, require_ok))
        reply = self.query(command)
        if require_ok and reply != "OK":
            raise ConnectionError(f"Unexpected reply: {reply}")
        return reply


def test_parse_float_reply_accepts_decimal_comma_and_rejects_errors():
    assert parse_float_reply("23.56") == pytest.approx(23.56)
    assert parse_float_reply("1,25") == pytest.approx(1.25)
    with pytest.raises(ConnectionError, match="ERR"):
        parse_float_reply("ERR_2")


def test_status_reply_helpers():
    assert standby_reply_to_running("1") is False
    assert standby_reply_to_running("0") is True
    assert standby_reply_to_running("UNKNOWN", fallback=True) is True
    assert fault_reply_to_bool("OK") is False
    assert fault_reply_to_bool("0000") is False
    assert fault_reply_to_bool("0004") is True


def test_ecosilver_read_state_and_setters():
    connection = FakeConnection(
        {
            "IN_SP_00": "23.56",
            "IN_PV_00": "22.90",
            "IN_PV_01": "23.10",
            "IN_PV_02": "0.65",
            "IN_PV_03": "24.20",
            "IN_MODE_02": "0",
            "IN_SP_01": "3",
            "IN_SP_02": "1",
            "IN_MODE_06": "0",
            "STATUS": "OK",
            "STAT": "0000",
            "OUT_SP_00_21.50": "OK",
            "START": "OK",
            "STOP": "OK",
        }
    )
    driver = ECOSilverRE1225SDriver(connection=connection)

    state = driver.read_state()
    assert state.temperature_c == pytest.approx(23.10)
    assert state.bath_temperature_c == pytest.approx(22.90)
    assert state.controlled_temperature_c == pytest.approx(23.10)
    assert state.external_temperature_c == pytest.approx(24.20)
    assert state.pressure_bar == pytest.approx(0.65)
    assert state.setpoint_c == pytest.approx(23.56)
    assert state.running is True
    assert state.fault is False
    assert state.pump_stage == "3"
    assert state.cooling_mode == "1"
    assert state.safe_mode_status == "0"
    assert state.standby_status == "0"
    assert state.device_status == "OK"
    assert state.fault_diagnosis == "0000"

    driver.set_setpoint(21.5)
    driver.set_running(True)
    driver.set_running(False)

    assert ("command", "OUT_SP_00_21.50", True) in connection.calls
    assert ("command", "START", False) in connection.calls
    assert ("command", "STOP", False) in connection.calls


def test_ecosilver_optional_external_temperature_and_pressure_preserve_last_values():
    connection = FakeConnection(
        {
            "IN_SP_00": "20.00",
            "IN_PV_00": "19.90",
            "IN_PV_01": "20.10",
            "IN_PV_02": OSError("pressure unavailable"),
            "IN_PV_03": OSError("external probe unavailable"),
            "IN_MODE_02": "1",
            "IN_SP_01": "",
            "IN_SP_02": "",
            "IN_MODE_06": "",
            "STATUS": "OK",
            "STAT": "0000",
        }
    )
    driver = ECOSilverRE1225SDriver(connection=connection)

    state = driver.read_state()

    assert state.pressure_bar == pytest.approx(0.0)
    assert state.external_temperature_c == pytest.approx(0.0)
    assert state.running is False


def test_ecosilver_ping_reports_type_failure():
    connection = FakeConnection({"TYPE": OSError("network unreachable")})
    driver = ECOSilverRE1225SDriver(connection=connection)

    assert driver.ping() is False
    assert isinstance(driver.last_error, OSError)


def test_build_ecosilver_driver_rejects_missing_host():
    with pytest.raises(ConfigurationError, match="requires host"):
        build_ecosilver_re_1225s_driver({})
