"""TDK-Lambda GENH600 serial high-voltage power-supply driver."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any

from ...config import ConfigurationError
from ..base import HighVoltageDriver, PowerChannelState


DEFAULT_BAUDRATE = 9600
DEFAULT_ADDRESS = 6
DEFAULT_TIMEOUT = 2.0
SUPPORTED_CHANNELS = {1}


def parse_float_reply(reply: str) -> float:
    """Parse a GENH600 numeric ASCII reply."""
    try:
        return float(reply.strip().replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"Cannot parse numeric value from GENH600 reply: {reply!r}") from exc


class GENH600SerialConnection:
    """Small pyserial wrapper for the GENH600 CR-terminated ASCII protocol."""

    def __init__(
        self,
        port: str,
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.port = port
        self.baudrate = int(baudrate)
        self.timeout = float(timeout)
        self._serial = None

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def command(self, command: str) -> None:
        reply = self._send(command)
        if reply != "OK":
            raise ConnectionError(f"Unexpected GENH600 reply to {command!r}: {reply!r}")

    def query(self, command: str) -> str:
        return self._send(command)

    def _open(self):
        if self._serial is None:
            try:
                import serial
            except ImportError as exc:
                raise RuntimeError(
                    "GENH600 hardware support requires the pyserial package"
                ) from exc

            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout,
            )
            if hasattr(self._serial, "reset_input_buffer"):
                self._serial.reset_input_buffer()
            if hasattr(self._serial, "reset_output_buffer"):
                self._serial.reset_output_buffer()
        return self._serial

    def _send(self, command: str) -> str:
        serial_port = self._open()
        try:
            serial_port.write(f"{command.strip()}\r".encode("ascii"))
            answer = serial_port.readline()
        except OSError:
            self.close()
            raise
        if not answer:
            self.close()
            raise TimeoutError(f"GENH600 timed out waiting for reply to {command!r}")
        return answer.decode("ascii", errors="replace").strip()


@dataclass
class _CachedChannel:
    voltage_setpoint: float = 0.0
    current_limit: float = 0.001
    output_enabled: bool = False
    ovp: float = 2500.0
    ocp: float = 0.01


@dataclass
class GENH600Driver(HighVoltageDriver):
    """High-voltage driver for one single-output GENH600 unit."""

    connection: GENH600SerialConnection
    channels: list[int]
    address: int = DEFAULT_ADDRESS
    clear_status: bool = True
    remote_control: bool = True
    identify_on_connect: bool = True
    initial_current_limit: float = 0.001
    initial_ovp: float = 2500.0
    initial_ocp: float = 0.01
    last_error: Exception | None = field(init=False, default=None)

    simulation = False

    def __post_init__(self) -> None:
        self.channels = [int(channel) for channel in self.channels]
        unsupported = sorted(set(self.channels).difference(SUPPORTED_CHANNELS))
        if unsupported:
            raise ConfigurationError(
                "GENH600 supports only channel 1; "
                f"invalid channels: {', '.join(str(item) for item in unsupported)}"
            )
        if not self.channels:
            raise ConfigurationError("GENH600 configuration requires channel 1")

        self._lock = threading.RLock()
        self._initialized = False
        self._cache = {
            1: _CachedChannel(
                current_limit=float(self.initial_current_limit),
                ovp=float(self.initial_ovp),
                ocp=float(self.initial_ocp),
            )
        }

    def initialize(self) -> None:
        """Initialize serial communication without changing output state or setpoints."""
        with self._lock:
            try:
                self.connection.command(f"ADR {int(self.address)}")
                if self.clear_status:
                    self.connection.command("CLS")
                if self.remote_control:
                    self.connection.command("RMT 1")
                if self.identify_on_connect:
                    self.connection.query("IDN?")
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
                    self.connection.query("IDN?")
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
                voltage = parse_float_reply(self.connection.query("MV?"))
                current = parse_float_reply(self.connection.query("MC?"))
            except Exception as exc:
                self._initialized = False
                self.last_error = exc
                self.connection.close()
                raise

            cached = self._cache[channel]
            self.last_error = None
            return PowerChannelState(
                voltage=voltage,
                current=current,
                current_limit=cached.current_limit,
                output_enabled=cached.output_enabled,
                ovp=cached.ovp,
                ocp=cached.ocp,
            )

    def set_voltage(self, channel: int, value: float) -> None:
        channel = self._require_channel(channel)
        if value < 0:
            raise ValueError("Voltage must be non-negative")
        with self._lock:
            self._command(f"PV {float(value):.6g}")
            self._cache[channel].voltage_setpoint = float(value)

    def set_current_limit(self, channel: int, value: float) -> None:
        channel = self._require_channel(channel)
        if value < 0:
            raise ValueError("Current limit must be non-negative")
        with self._lock:
            self._command(f"PC {float(value):.6g}")
            self._cache[channel].current_limit = float(value)

    def set_output(self, channel: int, enabled: bool) -> None:
        channel = self._require_channel(channel)
        with self._lock:
            self._command(f"OUT {1 if enabled else 0}")
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
            self._command("OUT 0")
            self._cache[1].output_enabled = False

    def all_outputs_off(self) -> bool:
        return all(not state.output_enabled for state in self._cache.values())

    def _require_channel(self, channel: int) -> int:
        channel = int(channel)
        if channel not in self._cache:
            raise ValueError(f"Unknown GENH600 channel: {channel}")
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


def build_genh600_driver(config: dict[str, Any]) -> GENH600Driver:
    """Build a GENH600 driver from one HV device JSON object."""
    port = str(config.get("port", "")).strip()
    if not port:
        raise ConfigurationError("GENH600 hardware configuration requires port")

    channels = [int(value) for value in config.get("channels", [])]
    return GENH600Driver(
        connection=GENH600SerialConnection(
            port=port,
            baudrate=int(config.get("baudrate", DEFAULT_BAUDRATE)),
            timeout=float(config.get("timeout", DEFAULT_TIMEOUT)),
        ),
        channels=channels,
        address=int(config.get("address", DEFAULT_ADDRESS)),
        clear_status=bool(config.get("clear_status", True)),
        remote_control=bool(config.get("remote_control", True)),
        identify_on_connect=bool(config.get("identify_on_connect", True)),
        initial_current_limit=float(config.get("initial_current_limit", 0.001)),
        initial_ovp=float(config.get("initial_ovp", 2500.0)),
        initial_ocp=float(config.get("initial_ocp", 0.01)),
    )
