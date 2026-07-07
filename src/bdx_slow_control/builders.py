"""Build IOC PV databases from JSON configuration."""

from __future__ import annotations

from typing import Any

from .config import (
    ConfigurationError,
    normalized_prefix,
    require_list,
    require_mapping,
    server_settings,
)
from .context import PrototypeContext
from .drivers.factory import (
    build_chiller_driver,
    build_daq_driver,
    build_hv_driver,
    build_psu_driver,
    build_sensor_driver,
)
from .iocs.chiller import ChillerIOC
from .iocs.daq import DaqCrateIOC
from .iocs.environment import EnvironmentalSensorIOC, EnvironmentSummaryIOC
from .iocs.global_system import GlobalIOC
from .iocs.power import LowVoltagePowerChannelIOC, PowerChannelIOC, PowerChannelLimits, PowerDeviceIOC
from .runtime import RuntimeSettings
from .util import merge_pvdb


def _context_or_default(
    context: PrototypeContext | None,
    poll_interval: float,
) -> PrototypeContext:
    return context or PrototypeContext(
        RuntimeSettings(
            initial_update_period=float(poll_interval),
        )
    )


def build_psu(config: dict[str, Any], context: PrototypeContext | None = None):
    settings = server_settings(config)
    context = _context_or_default(context, settings.poll_interval)
    groups = []
    for device in _power_devices(config, subsystem="PSU"):
        prefix = normalized_prefix(device.get("prefix"))
        driver = build_psu_driver(device)
        context.register_all_off(driver.all_off)
        groups.append(
            PowerDeviceIOC(
                prefix=prefix,
                driver=driver,
                runtime_settings=context.runtime,
            )
        )
        for channel in device.get("channels", []):
            channel_number = int(channel)
            groups.append(
                LowVoltagePowerChannelIOC(
                    prefix=f"{prefix}CH{channel_number}:",
                    driver=driver,
                    channel=channel_number,
                    limits=_power_channel_limits(device, channel_number),
                    runtime_settings=context.runtime,
                )
            )
    return merge_pvdb(groups), settings


def _power_devices(config: dict[str, Any], *, subsystem: str) -> list[dict[str, Any]]:
    if "device" in config and "devices" in config:
        raise ConfigurationError(
            f"{subsystem} configuration must use either device or devices, not both"
        )
    if "devices" in config:
        devices = require_list(config, "devices")
        if not devices:
            raise ConfigurationError(f"{subsystem} devices list must not be empty")
        if not all(isinstance(device, dict) for device in devices):
            raise ConfigurationError(f"Each {subsystem} device entry must be an object")
        return devices
    return [require_mapping(config, "device")]


def _power_channel_limits(device: dict[str, Any], channel: int) -> PowerChannelLimits:
    raw_limits: dict[str, Any] = {}
    device_limits = device.get("software_limits", {})
    if isinstance(device_limits, dict):
        raw_limits.update(device_limits)
    channel_limits = device.get("channel_limits", {})
    if isinstance(channel_limits, dict):
        specific = channel_limits.get(str(channel), channel_limits.get(channel, {}))
        if isinstance(specific, dict):
            raw_limits.update(specific)

    return PowerChannelLimits(
        minimum_voltage=float(raw_limits.get("minimum_voltage", 0.0)),
        maximum_voltage=float(raw_limits.get("maximum_voltage", 60.0)),
        minimum_current_limit=float(raw_limits.get("minimum_current_limit", 0.0)),
        maximum_current_limit=float(raw_limits.get("maximum_current_limit", 20.0)),
        maximum_power=float(raw_limits.get("maximum_power", 420.0)),
    )


def build_hv(config: dict[str, Any], context: PrototypeContext | None = None):
    settings = server_settings(config)
    context = _context_or_default(context, settings.poll_interval)
    groups = []
    for device in _power_devices(config, subsystem="HV"):
        prefix = normalized_prefix(device.get("prefix"))
        driver = build_hv_driver(device)
        context.register_all_off(driver.all_off)
        groups.append(
            PowerDeviceIOC(
                prefix=prefix,
                driver=driver,
                runtime_settings=context.runtime,
            )
        )
        for channel in device.get("channels", []):
            groups.append(
                PowerChannelIOC(
                    prefix=f"{prefix}CH{int(channel)}:",
                    driver=driver,
                    channel=int(channel),
                    runtime_settings=context.runtime,
                )
            )
    return merge_pvdb(groups), settings


def build_chiller(config: dict[str, Any], context: PrototypeContext | None = None):
    settings = server_settings(config)
    context = _context_or_default(context, settings.poll_interval)
    device = require_mapping(config, "device")
    group = ChillerIOC(
        prefix=normalized_prefix(device.get("prefix")),
        driver=build_chiller_driver(device),
        runtime_settings=context.runtime,
        minimum_setpoint_c=float(device.get("minimum_setpoint_c", 5.0)),
        maximum_setpoint_c=float(device.get("maximum_setpoint_c", 40.0)),
        warning_deviation_c=float(device.get("warning_deviation_c", 0.2)),
        alarm_deviation_c=float(device.get("alarm_deviation_c", 0.5)),
    )
    return group.pvdb, settings


def build_environment(config: dict[str, Any], context: PrototypeContext | None = None):
    settings = server_settings(config)
    context = _context_or_default(context, settings.poll_interval)
    summary_config = config.get("summary", {})
    if summary_config is not None and not isinstance(summary_config, dict):
        raise ConfigurationError("environment summary must be an object when provided")
    summary = EnvironmentSummaryIOC(
        prefix=normalized_prefix((summary_config or {}).get("prefix", "BDX:ENV:")),
        runtime_settings=context.runtime,
    )
    groups = [summary]
    for raw_sensor in require_list(config, "sensors"):
        if not isinstance(raw_sensor, dict):
            raise ConfigurationError("Each sensor entry must be an object")
        groups.append(
            EnvironmentalSensorIOC(
                prefix=normalized_prefix(raw_sensor.get("prefix")),
                driver=build_sensor_driver(raw_sensor),
                unit=str(raw_sensor.get("unit", "")),
                sensor_kind=str(raw_sensor.get("kind", "unknown")),
                summary=summary,
                runtime_settings=context.runtime,
            )
        )
    return merge_pvdb(groups), settings


def build_daq(config: dict[str, Any], context: PrototypeContext | None = None):
    settings = server_settings(config)
    context = _context_or_default(context, settings.poll_interval)
    groups = []
    for raw_crate in require_list(config, "crates"):
        if not isinstance(raw_crate, dict):
            raise ConfigurationError("Each DAQ crate entry must be an object")
        groups.append(
            DaqCrateIOC(
                prefix=normalized_prefix(raw_crate.get("prefix")),
                driver=build_daq_driver(raw_crate),
                runtime_settings=context.runtime,
            )
        )
    return merge_pvdb(groups), settings


def build_global(config: dict[str, Any], context: PrototypeContext | None = None):
    settings = server_settings(config)
    system = require_mapping(config, "system")
    if context is None:
        context = PrototypeContext(
            RuntimeSettings(
                initial_update_period=float(
                    system.get("initial_update_period", settings.poll_interval)
                ),
                minimum_update_period=float(system.get("minimum_update_period", 2.0)),
                maximum_update_period=float(system.get("maximum_update_period", 3600.0)),
            )
        )
    group = GlobalIOC(
        prefix=normalized_prefix(system.get("prefix")),
        runtime_settings=context.runtime,
        initial_state=str(system.get("initial_state", "STANDBY")),
        all_off_callbacks=context.all_off_callbacks,
    )
    return group.pvdb, settings


BUILDERS = {
    "psu": build_psu,
    "chiller": build_chiller,
    "environment": build_environment,
    "hv": build_hv,
    "daq": build_daq,
    "global": build_global,
}
