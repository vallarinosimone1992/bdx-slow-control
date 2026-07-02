import io
import json
import os
from pathlib import Path

import pytest

from bdx_slow_control.environment_check import (
    check_environment,
    format_result,
    main as environment_check_main,
)


class FakeBus:
    def __init__(self, data: bytes = bytes([0x01, 0x90]), fail_read: bool = False):
        self.data = data
        self.fail_read = fail_read
        self.writes = []
        self.reads = []
        self.closed = False

    def write_register(self, address, register, *values):
        self.writes.append((address, register, values))

    def read_register(self, address, register, length):
        self.reads.append((address, register, length))
        if self.fail_read:
            raise OSError("sensor did not acknowledge")
        return self.data

    def close(self):
        self.closed = True


def _config(address: str = "0x18") -> dict:
    return {
        "server": {"interfaces": ["0.0.0.0"], "poll_interval": 5.0},
        "sensors": [
            {
                "name": "T00",
                "prefix": "BDX:ENV:TEMP:T00:",
                "kind": "temperature",
                "unit": "degC",
                "mode": "hardware",
                "driver": "mcp9808",
                "bus": "/dev/i2c-1",
                "address": address,
                "resolution_c": 0.0625,
            }
        ],
    }


def _write_config(tmp_path: Path, config: dict | None = None) -> Path:
    path = tmp_path / "environment.json"
    path.write_text(json.dumps(config or _config()), encoding="utf-8")
    return path


def _open_ok(path: str, flags: int) -> int:
    assert path == "/dev/i2c-1"
    assert flags == os.O_RDWR
    return 12


def _close_ok(fd: int) -> None:
    assert fd == 12


def test_environment_check_successful_read_reports_temperature_and_configured_address_only():
    bus = FakeBus()
    results = check_environment(
        _config(address="0x1B"),
        device_exists=lambda path: path == "/dev/i2c-1",
        open_device=_open_ok,
        close_device=_close_ok,
        bus_factory=lambda path: bus,
    )

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].temperature_c == pytest.approx(25.0)
    assert bus.writes[0][0] == 0x1B
    assert bus.reads == [(0x1B, 0x05, 2)]
    assert "connectivity=OK" in format_result(results[0])
    assert "temperature_c=25.0000" in format_result(results[0])


def test_environment_check_reports_missing_i2c_device_path():
    results = check_environment(
        _config(),
        device_exists=lambda path: False,
        open_device=_open_ok,
        close_device=_close_ok,
        bus_factory=lambda path: FakeBus(),
    )

    assert results[0].ok is False
    assert "I2C device does not exist: /dev/i2c-1" in results[0].error


def test_environment_check_reports_permission_denied():
    def open_denied(path: str, flags: int) -> int:
        raise PermissionError("permission denied")

    results = check_environment(
        _config(),
        device_exists=lambda path: True,
        open_device=open_denied,
        close_device=_close_ok,
        bus_factory=lambda path: FakeBus(),
    )

    assert results[0].ok is False
    assert "Permission denied opening I2C device for read/write" in results[0].error


def test_environment_check_reports_disconnected_sensor():
    results = check_environment(
        _config(),
        device_exists=lambda path: True,
        open_device=_open_ok,
        close_device=_close_ok,
        bus_factory=lambda path: FakeBus(fail_read=True),
    )

    assert results[0].ok is False
    assert "Cannot read MCP9808 sensor T00" in results[0].error
    assert "sensor did not acknowledge" in results[0].error


def test_environment_check_command_exit_code_success(tmp_path: Path):
    config_path = _write_config(tmp_path)
    output = io.StringIO()

    exit_code = environment_check_main(
        ["--config", str(config_path)],
        output=output,
        device_exists=lambda path: True,
        open_device=_open_ok,
        close_device=_close_ok,
        bus_factory=lambda path: FakeBus(),
    )

    assert exit_code == 0
    assert "sensor=T00" in output.getvalue()
    assert "connectivity=OK" in output.getvalue()


def test_environment_check_command_exit_code_failure(tmp_path: Path):
    config_path = _write_config(tmp_path)
    output = io.StringIO()

    exit_code = environment_check_main(
        ["--config", str(config_path)],
        output=output,
        device_exists=lambda path: False,
        open_device=_open_ok,
        close_device=_close_ok,
        bus_factory=lambda path: FakeBus(),
    )

    assert exit_code == 1
    assert "connectivity=ERROR" in output.getvalue()
