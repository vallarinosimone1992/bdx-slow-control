"""TTi CPX400DP TCP/IP power-supply driver."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import socket
import threading
from typing import Any

from ...config import ConfigurationError
from ..base import PowerChannelState, PowerSupplyDriver


DEFAULT_PORT = 9221
DEFAULT_TIMEOUT = 3.0
SUPPORTED_CHANNELS = {1, 2}

_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def parse_numeric_reply(reply: str) -> float:
    """Extract the last numeric value from a CPX400DP ASCII reply."""
    matches = _NUMBER_RE.findall(reply.replace(",", "."))
    if not matches:
        raise ValueError(f"Cannot parse numeric value from CPX400DP reply: {reply!r}")
    return float(matches[-1])


class CPX400DPConnection:
    """Small TCP client for the CPX400DP newline-terminated ASCII protocol."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self._socket: socket.socket | None = None

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def _connect(self) -> socket.socket:
        if self._socket is None:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            sock.settimeout(self.timeout)
            self._socket = sock
        return self._socket

    def command(self, command: str) -> None:
        self._send(command)

    def query(self, command: str) -> str:
        sock = self._send(command)
        try:
            data = sock.recv(4096)
        except OSError:
            self.close()
            raise
        if not data:
            self.close()
            raise ConnectionError(f"CPX400DP at {self.host}:{self.port} closed the connection")
        return data.decode("ascii", errors="replace").strip()

    def _send(self, command: str) -> socket.socket:
        sock = self._connect()
        payload = f"{command.strip()}\n".encode("ascii")
        try:
            sock.sendall(payload)
        except OSError:
            self.close()
            raise
        return sock


@dataclass
class _CachedChannel:
    voltage_setpoint: float = 0.0
    current_limit: float = 0.5
    output_enabled: bool = False
    ovp: float = 10.0
    ocp: float = 1.0


@dataclass
class CPX400DPDriver(PowerSupplyDriver):
    """Power-supply driver for one dual-channel CPX400DP unit."""

    connection: CPX400DPConnection
    channels: list[int]
    lock_interface: bool = True
    clear_status: bool = True
    configure_independent: bool = False
    initial_current_limit: float = 0.5
    initial_ovp: float = 10.0
    initial_ocp: float = 1.0
    last_error: Exception | None = field(init=False, default=None)

    simulation = False

    def __post_init__(self) -> None:
        self.channels = [int(channel) for channel in self.channels]
        unsupported = sorted(set(self.channels).difference(SUPPORTED_CHANNELS))
        if unsupported:
            raise ConfigurationError(
                "CPX400DP supports only channels 1 and 2; "
                f"invalid channels: {', '.join(str(item) for item in unsupported)}"
            )
        if not self.channels:
            raise ConfigurationError("CPX400DP configuration requires at least one channel")

        self._lock = threading.RLock()
        self._initialized = False
        self._cache = {
            channel: _CachedChannel(
                current_limit=float(self.initial_current_limit),
                ovp=float(self.initial_ovp),
                ocp=float(self.initial_ocp),
            )
            for channel in self.channels
        }

    def initialize(self) -> None:
        """Initialize communication without changing output states."""
        with self._lock:
            try:
                self.connection.query("*IDN?")
                if self.lock_interface:
                    lock_reply = self.connection.query("IFLOCK")
                    if lock_reply.strip() != "1":
                        raise ConnectionError(
                            "CPX400DP interface lock was not granted; "
                            f"reply was {lock_reply!r}"
                        )
                if self.clear_status:
                    self.connection.command("*CLS")
                if self.configure_independent:
                    self.connection.command("CONFIG 2")
            except Exception as exc:
                self._initialized = False
                self.last_error = exc
                self.connection.close()
                raise
            else:
                self._initialized = True
                self.last_error = None

    def ping(self) -> bool:
        with self._lock:
            try:
                if not self._initialized:
                    self.initialize()
                else:
                    self.connection.query("*IDN?")
            except Exception as exc:
                self._initialized = False
                self.last_error = exc
                self.connection.close()
                return False
            self.last_error = None
            return True

    def read_channel(self, channel: int) -> PowerChannelState:
        channel = self._require_channel(channel)
        with self._lock:
            try:
                self._ensure_initialized()
                voltage = self._query_numeric(f"V{channel}O?")
                current = self._query_numeric(f"I{channel}O?")
                current_limit = self._query_numeric(f"I{channel}?")
            except Exception as exc:
                self._initialized = False
                self.last_error = exc
                self.connection.close()
                raise

            cached = self._cache[channel]
            cached.current_limit = current_limit
            self.last_error = None
            return PowerChannelState(
                voltage=voltage,
                current=current,
                current_limit=current_limit,
                output_enabled=cached.output_enabled,
                ovp=cached.ovp,
                ocp=cached.ocp,
            )

    def set_voltage(self, channel: int, value: float) -> None:
        channel = self._require_channel(channel)
        if value < 0:
            raise ValueError("Voltage must be non-negative")
        with self._lock:
            self._command(f"V{channel} {float(value):.6g}")
            self._cache[channel].voltage_setpoint = float(value)

    def set_current_limit(self, channel: int, value: float) -> None:
        channel = self._require_channel(channel)
        if value < 0:
            raise ValueError("Current limit must be non-negative")
        with self._lock:
            self._command(f"I{channel} {float(value):.6g}")
            self._cache[channel].current_limit = float(value)

    def set_output(self, channel: int, enabled: bool) -> None:
        channel = self._require_channel(channel)
        with self._lock:
            self._command(f"OP{channel} {1 if enabled else 0}")
            self._cache[channel].output_enabled = bool(enabled)

    def set_ovp(self, channel: int, value: float) -> None:
        channel = self._require_channel(channel)
        if value <= 0:
            raise ValueError("OVP must be positive")
        self._cache[channel].ovp = float(value)

    def set_ocp(self, channel: int, value: float) -> None:
        channel = self._require_channel(channel)
        if value <= 0:
            raise ValueError("OCP must be positive")
        self._cache[channel].ocp = float(value)

    def all_off(self) -> None:
        with self._lock:
            self._command("OPALL 0")
            for state in self._cache.values():
                state.output_enabled = False

    def all_outputs_off(self) -> bool:
        return all(not state.output_enabled for state in self._cache.values())

    def _require_channel(self, channel: int) -> int:
        channel = int(channel)
        if channel not in self._cache:
            raise ValueError(f"Unknown CPX400DP channel: {channel}")
        return channel

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self.initialize()

    def _command(self, command: str) -> None:
        try:
            self._ensure_initialized()
            self.connection.command(command)
        except Exception as exc:
            self._initialized = False
            self.last_error = exc
            self.connection.close()
            raise
        self.last_error = None

    def _query_numeric(self, command: str) -> float:
        return parse_numeric_reply(self.connection.query(command))


def build_cpx400dp_driver(config: dict[str, Any]) -> CPX400DPDriver:
    """Build a CPX400DP driver from one PSU device JSON object."""
    host = str(config.get("host", "")).strip()
    if not host:
        raise ConfigurationError("CPX400DP hardware configuration requires host")

    channels = [int(value) for value in config.get("channels", [])]
    return CPX400DPDriver(
        connection=CPX400DPConnection(
            host=host,
            port=int(config.get("port", DEFAULT_PORT)),
            timeout=float(config.get("timeout", DEFAULT_TIMEOUT)),
        ),
        channels=channels,
        lock_interface=bool(config.get("lock_interface", True)),
        clear_status=bool(config.get("clear_status", True)),
        configure_independent=bool(config.get("configure_independent", False)),
        initial_current_limit=float(config.get("initial_current_limit", 0.5)),
        initial_ovp=float(config.get("initial_ovp", 10.0)),
        initial_ocp=float(config.get("initial_ocp", 1.0)),
    )
