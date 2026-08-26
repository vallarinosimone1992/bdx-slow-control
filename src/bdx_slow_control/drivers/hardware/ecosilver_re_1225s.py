"""LAUDA ECO Silver RE 1225 S TCP/IP chiller driver."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import socket
import threading
import time
from typing import Any

from ...config import ConfigurationError
from ..base import ChillerDriver, ChillerState


DEFAULT_PORT = 54321
DEFAULT_TIMEOUT = 5.0
DEFAULT_RECONNECT_DELAY = 15.0
MAX_REPLY_BYTES = 4096


@dataclass(frozen=True)
class LaudaStat:
    raw: str
    general_error: bool
    general_alarm: bool
    general_warning: bool
    overtemperature: bool
    low_level: bool
    reserved: bool
    external_control_missing: bool


def parse_float_reply(reply: str) -> float:
    """Parse a LAUDA numeric ASCII reply."""
    if reply.startswith("ERR"):
        raise ConnectionError(f"LAUDA command returned {reply!r}")
    try:
        return float(reply.strip().replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"Cannot parse numeric value from LAUDA reply: {reply!r}") from exc


def standby_reply_to_running(reply: str, fallback: bool = False) -> bool:
    """Convert the LAUDA standby status reply into a running boolean."""
    normalized = reply.strip().upper()
    if normalized.startswith("ERR"):
        raise ConnectionError(f"LAUDA standby query returned {reply!r}")
    if normalized in {"1", "ON", "TRUE", "YES", "STANDBY"}:
        return False
    if normalized in {"0", "OFF", "FALSE", "NO", "RUN", "RUNNING"}:
        return True
    return fallback


def parse_stat_reply(reply: str) -> LaudaStat:
    """Decode the seven binary flags returned by the LAUDA ``STAT`` command."""
    raw = reply.strip()
    if len(raw) != 7 or any(bit not in "01" for bit in raw):
        raise ValueError(
            "LAUDA STAT reply must contain exactly seven binary characters; "
            f"received {reply!r}"
        )
    flags = tuple(bit == "1" for bit in raw)
    return LaudaStat(raw, *flags)


def fault_reply_to_bool(reply: str) -> bool:
    """Return whether any LAUDA ``STAT`` flag is active."""
    status = parse_stat_reply(reply)
    return any(
        (
            status.general_error,
            status.general_alarm,
            status.general_warning,
            status.overtemperature,
            status.low_level,
            status.reserved,
            status.external_control_missing,
        )
    )


class LAUDAConnection:
    """Persistent TCP client for the LAUDA CRLF-terminated ASCII protocol."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        reconnect_delay: float = DEFAULT_RECONNECT_DELAY,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.reconnect_delay = float(reconnect_delay)
        if self.reconnect_delay < 0:
            raise ValueError("LAUDA reconnect delay must be non-negative")
        self._socket: socket.socket | None = None
        self._receive_buffer = bytearray()
        self._retry_after = 0.0
        self._lock = threading.RLock()

    def close(self) -> None:
        """Close the persistent connection without imposing reconnect backoff."""
        with self._lock:
            self._close_socket()
            self._retry_after = 0.0

    def _close_socket(self) -> None:
        sock = self._socket
        self._socket = None
        self._receive_buffer.clear()
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _mark_transport_failure(self) -> None:
        self._close_socket()
        self._retry_after = time.monotonic() + self.reconnect_delay

    def _connect(self) -> socket.socket:
        if self._socket is not None:
            return self._socket

        remaining = self._retry_after - time.monotonic()
        if remaining > 0:
            raise ConnectionError(
                "LAUDA chiller reconnect deferred after a transport failure "
                f"for another {remaining:.1f} s"
            )

        sock: socket.socket | None = None
        try:
            sock = socket.create_connection(
                (self.host, self.port),
                timeout=self.timeout,
            )
            sock.settimeout(self.timeout)
        except OSError as exc:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            self._mark_transport_failure()
            raise ConnectionError(
                f"LAUDA chiller communication failed for {self.host}:{self.port}: {exc}"
            ) from exc

        self._socket = sock
        self._retry_after = 0.0
        return sock

    def _read_reply(self, sock: socket.socket) -> str:
        while True:
            terminator = self._receive_buffer.find(b"\r\n")
            if terminator >= 0:
                raw_reply = bytes(self._receive_buffer[:terminator])
                del self._receive_buffer[: terminator + 2]
                return raw_reply.decode("ascii", errors="replace").strip()

            if len(self._receive_buffer) >= MAX_REPLY_BYTES:
                raise ConnectionError(
                    "LAUDA chiller reply exceeded "
                    f"{MAX_REPLY_BYTES} bytes without a CRLF terminator"
                )

            response = sock.recv(min(1024, MAX_REPLY_BYTES - len(self._receive_buffer)))
            if not response:
                raise ConnectionError(
                    f"LAUDA chiller at {self.host}:{self.port} closed the connection"
                )
            self._receive_buffer.extend(response)

    def query(self, command: str) -> str:
        with self._lock:
            sock = self._connect()
            try:
                sock.sendall(f"{command.strip()}\r\n".encode("ascii"))
                return self._read_reply(sock)
            except (OSError, ConnectionError) as exc:
                self._mark_transport_failure()
                raise ConnectionError(
                    f"LAUDA chiller communication failed for {self.host}:{self.port}: {exc}"
                ) from exc

    def command(self, command: str, *, require_ok: bool = False) -> str:
        reply = self.query(command)
        if reply.startswith("ERR"):
            raise ConnectionError(f"LAUDA command {command!r} returned {reply!r}")
        if require_ok and reply != "OK":
            raise ConnectionError(f"Unexpected LAUDA reply to {command!r}: {reply!r}")
        return reply


@dataclass
class ECOSilverRE1225SDriver(ChillerDriver):
    """Chiller driver for one LAUDA ECO Silver RE 1225 S controller."""

    connection: LAUDAConnection
    bath_temperature_command: str = "IN_PV_00"
    controlled_temperature_command: str = "IN_PV_01"
    pressure_command: str = "IN_PV_02"
    external_temperature_command: str = "IN_PV_03"
    pump_stage_command: str = "IN_SP_01"
    cooling_mode_command: str = "IN_SP_02"
    safe_setpoint_read_command: str = "IN_SP_07"
    safe_setpoint_write_prefix: str = "OUT_SP_07"
    communication_timeout_read_command: str = "IN_SP_08"
    communication_timeout_write_prefix: str = "OUT_SP_08"
    setpoint_read_command: str = "IN_SP_00"
    setpoint_write_prefix: str = "OUT_SP_00"
    standby_command: str = "IN_MODE_02"
    device_status_command: str = "STATUS"
    fault_command: str = "STAT"
    pressure_enabled: bool = False
    external_temperature_enabled: bool = False
    pressure_required: bool = False
    external_temperature_required: bool = False
    minimum_setpoint_c: float | None = None
    maximum_setpoint_c: float | None = None
    last_error: Exception | None = field(init=False, default=None)

    simulation = False

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._last_pressure_bar = 0.0
        self._last_external_temperature_c = 0.0
        self._last_running = False

    def ping(self) -> bool:
        with self._lock:
            try:
                reply = self.connection.query("TYPE")
            except Exception as exc:
                self.last_error = exc
                return False
            if reply.startswith("ERR"):
                self.last_error = ConnectionError(f"LAUDA TYPE returned {reply!r}")
                return False
            self.last_error = None
            return True

    def read_state(self) -> ChillerState:
        with self._lock:
            try:
                setpoint_c = self._read_float(self.setpoint_read_command)
                bath_temperature_c = self._read_float(self.bath_temperature_command)
                controlled_temperature_c = self._read_float(self.controlled_temperature_command)
                external_temperature_c, external_temperature_valid = (
                    self._read_external_temperature()
                )
                pressure_bar, pressure_valid = self._read_pressure()
                standby_status = self.connection.query(self.standby_command)
                running = standby_reply_to_running(standby_status, fallback=self._last_running)
                pump_stage = self._query_optional(self.pump_stage_command)
                cooling_mode = self._query_optional(self.cooling_mode_command)
                safe_setpoint_c = self._read_optional_float(self.safe_setpoint_read_command)
                communication_timeout_s = self._read_optional_float(
                    self.communication_timeout_read_command
                )
                safe_mode_status = (
                    "AVAILABLE"
                    if math.isfinite(safe_setpoint_c)
                    and math.isfinite(communication_timeout_s)
                    else "UNAVAILABLE"
                )
                device_status = self._query_optional(self.device_status_command)
                fault_diagnosis = self._query_optional(self.fault_command)
                stat = parse_stat_reply(fault_diagnosis)
                fault = fault_reply_to_bool(fault_diagnosis)
            except Exception as exc:
                self.last_error = exc
                raise

            self._last_pressure_bar = pressure_bar
            self._last_external_temperature_c = external_temperature_c
            self._last_running = running
            self.last_error = None
            return ChillerState(
                temperature_c=controlled_temperature_c,
                setpoint_c=setpoint_c,
                pressure_bar=pressure_bar,
                running=running,
                fault=fault,
                bath_temperature_c=bath_temperature_c,
                controlled_temperature_c=controlled_temperature_c,
                external_temperature_c=external_temperature_c,
                pump_stage=pump_stage,
                cooling_mode=cooling_mode,
                safe_mode_status=safe_mode_status,
                standby_status=standby_status,
                device_status=device_status,
                fault_diagnosis=fault_diagnosis,
                pressure_enabled=self.pressure_enabled,
                pressure_valid=pressure_valid,
                external_temperature_enabled=self.external_temperature_enabled,
                external_temperature_valid=external_temperature_valid,
                safe_setpoint_c=safe_setpoint_c,
                communication_timeout_s=communication_timeout_s,
                stat_general_error=stat.general_error,
                stat_general_alarm=stat.general_alarm,
                stat_general_warning=stat.general_warning,
                stat_overtemperature=stat.overtemperature,
                stat_low_level=stat.low_level,
                stat_reserved=stat.reserved,
                stat_external_control_missing=stat.external_control_missing,
            )

    def set_setpoint(self, value_c: float) -> None:
        value_c = float(value_c)
        if self.minimum_setpoint_c is not None and value_c < self.minimum_setpoint_c:
            raise ValueError("Chiller setpoint is below the configured minimum")
        if self.maximum_setpoint_c is not None and value_c > self.maximum_setpoint_c:
            raise ValueError("Chiller setpoint is above the configured maximum")

        with self._lock:
            try:
                self.connection.command(
                    f"{self.setpoint_write_prefix}_{value_c:.2f}",
                    require_ok=True,
                )
            except Exception as exc:
                self.last_error = exc
                raise
            self.last_error = None

    def set_running(self, running: bool) -> None:
        requested_running = bool(running)
        with self._lock:
            try:
                command = "START" if requested_running else "STOP"
                self.connection.command(command, require_ok=True)
                standby_status = self.connection.query(self.standby_command)
                verified_running = standby_reply_to_running(
                    standby_status,
                    fallback=not requested_running,
                )
                if verified_running != requested_running:
                    expected = "running" if requested_running else "standby"
                    raise ConnectionError(
                        f"LAUDA {command} was acknowledged but the device did not enter "
                        f"{expected}; {self.standby_command} returned {standby_status!r}"
                    )
            except Exception as exc:
                self.last_error = exc
                raise
            self._last_running = verified_running
            self.last_error = None

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def set_safe_setpoint(self, value_c: float) -> None:
        value_c = float(value_c)
        with self._lock:
            try:
                self.connection.command(
                    f"{self.safe_setpoint_write_prefix}_{value_c:.2f}",
                    require_ok=True,
                )
            except Exception as exc:
                self.last_error = exc
                raise
            self.last_error = None

    def set_communication_timeout(self, value_s: float) -> None:
        value_s = float(value_s)
        if value_s < 0:
            raise ValueError("Communication timeout must be non-negative")
        with self._lock:
            try:
                self.connection.command(
                    f"{self.communication_timeout_write_prefix}_{value_s:.2f}",
                    require_ok=True,
                )
            except Exception as exc:
                self.last_error = exc
                raise
            self.last_error = None

    def _read_float(self, command: str) -> float:
        return parse_float_reply(self.connection.query(command))

    def _read_pressure(self) -> tuple[float, bool]:
        if not self.pressure_enabled:
            return math.nan, False
        try:
            return self._read_float(self.pressure_command), True
        except Exception:
            if self.pressure_required:
                raise
            return math.nan, False

    def _read_external_temperature(self) -> tuple[float, bool]:
        if not self.external_temperature_enabled:
            return math.nan, False
        try:
            return self._read_float(self.external_temperature_command), True
        except Exception:
            if self.external_temperature_required:
                raise
            return math.nan, False

    def _query_optional(self, command: str) -> str:
        if not command.strip():
            return ""
        try:
            return self.connection.query(command)
        except Exception:
            return ""

    def _read_optional_float(self, command: str) -> float:
        if not command.strip():
            return math.nan
        try:
            return self._read_float(command)
        except Exception:
            return math.nan


def build_ecosilver_re_1225s_driver(config: dict[str, Any]) -> ECOSilverRE1225SDriver:
    """Build a LAUDA ECO Silver RE 1225 S driver from one chiller JSON object."""
    host = str(config.get("host", "")).strip()
    if not host:
        raise ConfigurationError("LAUDA chiller hardware configuration requires host")

    minimum = config.get("minimum_setpoint_c", 5.0)
    maximum = config.get("maximum_setpoint_c", 40.0)
    return ECOSilverRE1225SDriver(
        connection=LAUDAConnection(
            host=host,
            port=int(config.get("port", DEFAULT_PORT)),
            timeout=float(config.get("timeout", DEFAULT_TIMEOUT)),
            reconnect_delay=float(config.get("reconnect_delay", DEFAULT_RECONNECT_DELAY)),
        ),
        bath_temperature_command=str(config.get("bath_temperature_command", "IN_PV_00")),
        controlled_temperature_command=str(
            config.get("controlled_temperature_command", "IN_PV_01")
        ),
        pressure_command=str(config.get("pressure_command", "IN_PV_02")),
        external_temperature_command=str(config.get("external_temperature_command", "IN_PV_03")),
        pump_stage_command=str(config.get("pump_stage_command", "IN_SP_01")),
        cooling_mode_command=str(config.get("cooling_mode_command", "IN_SP_02")),
        safe_setpoint_read_command=str(config.get("safe_setpoint_read_command", "IN_SP_07")),
        safe_setpoint_write_prefix=str(config.get("safe_setpoint_write_prefix", "OUT_SP_07")),
        communication_timeout_read_command=str(
            config.get("communication_timeout_read_command", "IN_SP_08")
        ),
        communication_timeout_write_prefix=str(
            config.get("communication_timeout_write_prefix", "OUT_SP_08")
        ),
        setpoint_read_command=str(config.get("setpoint_read_command", "IN_SP_00")),
        setpoint_write_prefix=str(config.get("setpoint_write_prefix", "OUT_SP_00")),
        standby_command=str(config.get("standby_command", "IN_MODE_02")),
        device_status_command=str(config.get("device_status_command", "STATUS")),
        fault_command=str(config.get("fault_command", "STAT")),
        pressure_enabled=bool(config.get("pressure_enabled", False)),
        external_temperature_enabled=bool(config.get("external_temperature_enabled", False)),
        pressure_required=bool(config.get("pressure_required", False)),
        external_temperature_required=bool(config.get("external_temperature_required", False)),
        minimum_setpoint_c=None if minimum is None else float(minimum),
        maximum_setpoint_c=None if maximum is None else float(maximum),
    )
