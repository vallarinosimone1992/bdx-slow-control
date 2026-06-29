"""Configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any


class ConfigurationError(ValueError):
    """Raised when an IOC configuration is invalid."""


@dataclass(frozen=True)
class ServerSettings:
    """Common Channel Access server settings."""

    interfaces: tuple[str, ...]
    poll_interval: float
    log_pv_names: bool


def load_json(path: str | Path) -> dict[str, Any]:
    """Load a JSON object from disk."""
    config_path = Path(path)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Configuration file not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Invalid JSON in {config_path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise ConfigurationError(f"Top-level JSON value must be an object: {config_path}")
    return data


def require_mapping(config: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a required mapping value."""
    value = config.get(key)
    if not isinstance(value, dict):
        raise ConfigurationError(f"Required object is missing or invalid: {key}")
    return value


def require_list(config: dict[str, Any], key: str) -> list[Any]:
    """Return a required list value."""
    value = config.get(key)
    if not isinstance(value, list):
        raise ConfigurationError(f"Required list is missing or invalid: {key}")
    return value


def normalized_prefix(value: Any) -> str:
    """Validate and normalize an EPICS PV prefix."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("PV prefix must be a non-empty string")
    prefix = value.strip()
    return prefix if prefix.endswith(":") else f"{prefix}:"


def server_settings(config: dict[str, Any]) -> ServerSettings:
    """Build server settings with optional environment overrides."""
    raw = config.get("server", {})
    if not isinstance(raw, dict):
        raise ConfigurationError("server must be a JSON object")

    interfaces = raw.get("interfaces", ["0.0.0.0"])
    if not isinstance(interfaces, list) or not all(isinstance(item, str) for item in interfaces):
        raise ConfigurationError("server.interfaces must be a list of strings")

    interface_override = os.getenv("BDX_EPICS_INTERFACE")
    if interface_override:
        interfaces = [interface_override]

    poll_interval = float(raw.get("poll_interval", 1.0))
    if poll_interval <= 0:
        raise ConfigurationError("server.poll_interval must be positive")

    return ServerSettings(
        interfaces=tuple(interfaces),
        poll_interval=poll_interval,
        log_pv_names=bool(raw.get("log_pv_names", False)),
    )
