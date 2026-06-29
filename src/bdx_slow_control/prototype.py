"""Build the complete single-host prototype IOC."""

from __future__ import annotations

from pathlib import Path

from .builders import BUILDERS
from .config import ConfigurationError, ServerSettings, load_json, require_mapping, server_settings
from .context import PrototypeContext
from .runtime import RuntimeSettings


def _build_context(config_dir: Path) -> PrototypeContext:
    global_path = config_dir / "global.json"
    if global_path.exists():
        config = load_json(global_path)
        settings = server_settings(config)
        system = require_mapping(config, "system")
        return PrototypeContext(
            RuntimeSettings(
                initial_update_period=float(
                    system.get("initial_update_period", settings.poll_interval)
                ),
                minimum_update_period=float(system.get("minimum_update_period", 2.0)),
                maximum_update_period=float(system.get("maximum_update_period", 3600.0)),
            )
        )
    return PrototypeContext(RuntimeSettings())


def build_prototype(config_dir: Path) -> tuple[dict, ServerSettings]:
    """Build all configured IOC groups in one caproto server database."""
    merged = {}
    selected_settings: ServerSettings | None = None
    context = _build_context(config_dir)

    for subsystem, builder in BUILDERS.items():
        path = config_dir / f"{subsystem}.json"
        if not path.exists():
            continue

        pvdb, settings = builder(load_json(path), context=context)
        overlap = set(merged).intersection(pvdb)
        if overlap:
            raise ConfigurationError(
                f"Duplicate PV names across configurations: {sorted(overlap)}"
            )

        if selected_settings is None:
            selected_settings = settings
        elif settings.interfaces != selected_settings.interfaces:
            raise ConfigurationError(
                "All subsystem configurations must use the same server.interfaces "
                "when running the aggregated prototype IOC"
            )

        merged.update(pvdb)

    if selected_settings is None:
        raise ConfigurationError(f"No subsystem configuration found in {config_dir}")

    return merged, selected_settings
