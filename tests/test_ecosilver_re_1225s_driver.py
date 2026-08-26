import pytest

from bdx_slow_control.config import ConfigurationError
from bdx_slow_control.drivers.hardware.ecosilver_re_1225s import (
    ECOSilverRE1225SDriver,
    LAUDAConnection,
    build_ecosilver_re_1225s_driver,
    fault_reply_to_bool,
    parse_stat_reply,
    parse_float_reply,
    standby_reply_to_running,
)


class FakeConnection:
    def __init__(self, replies=None, *, update_run_state=True):
        self.replies = replies or {}
        self.calls = []
        self.update_run_state = update_run_state

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
        if self.update_run_state and reply == "OK":
            if command == "START":
                self.replies["IN_MODE_02"] = "0"
            elif command == "STOP":
                self.replies["IN_MODE_02"] = "1"
        return reply


class FakeSocket:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.timeout = None
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendall(self, payload):
        self.sent.append(payload)

    def recv(self, size):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self):
        self.closed = True


def test_lauda_connection_reuses_one_socket_and_reads_crlf_frames(monkeypatch):
    sock = FakeSocket([b"EC", b"O\r", b"\n0\r\n"])
    connections = []

    def create_connection(address, timeout):
        connections.append((address, timeout))
        return sock

    monkeypatch.setattr("socket.create_connection", create_connection)
    connection = LAUDAConnection("192.0.2.10", timeout=2.5)

    assert connection.query("TYPE") == "ECO"
    assert connection.query("IN_MODE_02") == "0"

    assert connections == [(("192.0.2.10", 54321), 2.5)]
    assert sock.timeout == 2.5
    assert sock.sent == [b"TYPE\r\n", b"IN_MODE_02\r\n"]
    assert sock.closed is False

    connection.close()
    assert sock.closed is True


def test_lauda_connection_reconnects_after_transport_failure(monkeypatch):
    failed_socket = FakeSocket([ConnectionResetError("peer reset")])
    recovered_socket = FakeSocket([b"ECO\r\n"])
    sockets = iter([failed_socket, recovered_socket])
    connection_count = 0

    def create_connection(address, timeout):
        nonlocal connection_count
        connection_count += 1
        return next(sockets)

    monkeypatch.setattr("socket.create_connection", create_connection)
    connection = LAUDAConnection("192.0.2.10", reconnect_delay=0)

    with pytest.raises(ConnectionError, match="peer reset"):
        connection.query("TYPE")

    assert failed_socket.closed is True
    assert connection.query("TYPE") == "ECO"
    assert connection_count == 2


def test_lauda_connection_honors_reconnect_delay(monkeypatch):
    failed_socket = FakeSocket([ConnectionResetError("peer reset")])
    recovered_socket = FakeSocket([b"ECO\r\n"])
    sockets = iter([failed_socket, recovered_socket])
    now = [100.0]

    monkeypatch.setattr("socket.create_connection", lambda address, timeout: next(sockets))
    monkeypatch.setattr(
        "bdx_slow_control.drivers.hardware.ecosilver_re_1225s.time.monotonic",
        lambda: now[0],
    )
    connection = LAUDAConnection("192.0.2.10", reconnect_delay=15)

    with pytest.raises(ConnectionError, match="peer reset"):
        connection.query("TYPE")
    with pytest.raises(ConnectionError, match="deferred.*15.0 s"):
        connection.query("TYPE")

    now[0] = 115.0
    assert connection.query("TYPE") == "ECO"


def test_parse_float_reply_accepts_decimal_comma_and_rejects_errors():
    assert parse_float_reply("23.56") == pytest.approx(23.56)
    assert parse_float_reply("1,25") == pytest.approx(1.25)
    with pytest.raises(ConnectionError, match="ERR"):
        parse_float_reply("ERR_2")


def test_status_reply_helpers():
    assert standby_reply_to_running("1") is False
    assert standby_reply_to_running("0") is True
    assert standby_reply_to_running("UNKNOWN", fallback=True) is True
    assert fault_reply_to_bool("0000000") is False
    assert fault_reply_to_bool("0000100") is True


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
            "IN_SP_07": "18.00",
            "IN_SP_08": "10.00",
            "STATUS": "OK",
            "STAT": "0000000",
            "OUT_SP_00_21.50": "OK",
            "OUT_SP_07_18.50": "OK",
            "OUT_SP_08_12.00": "OK",
            "START": "OK",
            "STOP": "OK",
        }
    )
    driver = ECOSilverRE1225SDriver(
        connection=connection,
        pressure_enabled=True,
        external_temperature_enabled=True,
    )

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
    assert state.safe_mode_status == "AVAILABLE"
    assert state.safe_setpoint_c == pytest.approx(18.0)
    assert state.communication_timeout_s == pytest.approx(10.0)
    assert state.standby_status == "0"
    assert state.device_status == "OK"
    assert state.fault_diagnosis == "0000000"
    assert ("query", "IN_PV_00") in connection.calls
    assert ("query", "IN_PV_01") in connection.calls

    driver.set_setpoint(21.5)
    driver.set_safe_setpoint(18.5)
    driver.set_communication_timeout(12.0)
    driver.set_running(True)
    driver.set_running(False)

    assert ("command", "OUT_SP_00_21.50", True) in connection.calls
    assert ("command", "OUT_SP_07_18.50", True) in connection.calls
    assert ("command", "OUT_SP_08_12.00", True) in connection.calls
    assert ("command", "START", True) in connection.calls
    assert ("command", "STOP", True) in connection.calls
    assert connection.calls.count(("query", "IN_MODE_02")) == 3
    assert not any(
        call[1].startswith("OUT_MODE")
        for call in connection.calls
        if call[0] == "command"
    )


def test_stat_reply_decodes_all_seven_documented_bits():
    status = parse_stat_reply("1010101")

    assert status.general_error is True
    assert status.general_alarm is False
    assert status.general_warning is True
    assert status.overtemperature is False
    assert status.low_level is True
    assert status.reserved is False
    assert status.external_control_missing is True

    with pytest.raises(ValueError, match="seven binary"):
        parse_stat_reply("0000")


def test_ecosilver_run_command_requires_ok_reply():
    connection = FakeConnection({"STOP": "IGNORED"})
    driver = ECOSilverRE1225SDriver(connection=connection)

    with pytest.raises(ConnectionError, match="Unexpected reply"):
        driver.set_running(False)

    assert driver.last_error is not None


def test_ecosilver_run_command_verifies_standby_readback():
    connection = FakeConnection(
        {
            "STOP": "OK",
            "IN_MODE_02": "0",
        },
        update_run_state=False,
    )
    driver = ECOSilverRE1225SDriver(connection=connection)

    with pytest.raises(ConnectionError, match="did not enter standby"):
        driver.set_running(False)

    assert ("query", "IN_MODE_02") in connection.calls
    assert driver.last_error is not None


def test_ecosilver_disabled_external_temperature_and_pressure_are_not_queried():
    connection = FakeConnection(
        {
            "IN_SP_00": "20.00",
            "IN_PV_00": "19.90",
            "IN_PV_01": "20.10",
            "IN_MODE_02": "1",
            "IN_SP_01": "",
            "IN_SP_02": "",
            "IN_SP_07": "",
            "IN_SP_08": "",
            "STATUS": "OK",
            "STAT": "0000000",
        }
    )
    driver = ECOSilverRE1225SDriver(connection=connection)

    state = driver.read_state()

    assert state.pressure_enabled is False
    assert state.pressure_valid is False
    assert state.external_temperature_enabled is False
    assert state.external_temperature_valid is False
    assert ("query", "IN_PV_02") not in connection.calls
    assert ("query", "IN_PV_03") not in connection.calls
    assert state.running is False


def test_ecosilver_ping_and_read_state_perform_no_control_writes():
    connection = FakeConnection(
        {
            "TYPE": "ECO SILVER",
            "IN_SP_00": "20.00",
            "IN_PV_00": "19.90",
            "IN_PV_01": "20.10",
            "IN_MODE_02": "1",
            "IN_SP_01": "2",
            "IN_SP_02": "AUTO",
            "IN_SP_07": "18.00",
            "IN_SP_08": "10.00",
            "STATUS": "OK",
            "STAT": "0000000",
        }
    )
    driver = ECOSilverRE1225SDriver(connection=connection)

    assert driver.ping() is True
    driver.read_state()

    assert not any(call[0] == "command" for call in connection.calls)
    assert not any(
        call[1] in {"START", "STOP"} or call[1].startswith("OUT_SP_")
        for call in connection.calls
    )


def test_ecosilver_ping_reports_type_failure():
    connection = FakeConnection({"TYPE": OSError("network unreachable")})
    driver = ECOSilverRE1225SDriver(connection=connection)

    assert driver.ping() is False
    assert isinstance(driver.last_error, OSError)


def test_build_ecosilver_driver_rejects_missing_host():
    with pytest.raises(ConfigurationError, match="requires host"):
        build_ecosilver_re_1225s_driver({})


def test_build_ecosilver_driver_configures_reconnect_delay():
    driver = build_ecosilver_re_1225s_driver(
        {
            "host": "192.0.2.10",
            "reconnect_delay": 7.5,
        }
    )

    assert driver.connection.reconnect_delay == pytest.approx(7.5)
