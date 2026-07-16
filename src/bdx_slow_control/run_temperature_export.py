"""Parse CAEN run metadata and prepare a future temperature export."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
import re
from string import Formatter
import sys
from typing import Mapping, TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import ConfigurationError, load_json, require_list, require_mapping


DEFAULT_CONFIG = Path("config/examples/run_temperature_export.json")
DEFAULT_HISTOGRAM_BIN_WIDTH_SECONDS = 5.0
RUN_ROOT_ENV = "BDX_DAQ_RUN_ROOT"

ITALIAN_MONTHS = {
    "gen": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "mag": 5,
    "giu": 6,
    "lug": 7,
    "ago": 8,
    "set": 9,
    "ott": 10,
    "nov": 11,
    "dic": 12,
}

CAEN_TIMESTAMP_RE = re.compile(
    r"^\s*(?P<weekday>[^\W\d_]{2,})\s+"
    r"(?P<month>[^\W\d_]{3})\s+"
    r"(?P<day>\d{1,2})\s+"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})\s+"
    r"(?P<year>\d{4})\s*$",
    re.UNICODE,
)


class RunTemperatureExportError(RuntimeError):
    """Raised for an invalid run or CAEN metadata file."""


@dataclass(frozen=True)
class ArchiverSettings:
    """Archiver settings reserved for the future retrieval increment."""

    retrieval_url: str
    timeout_seconds: float


@dataclass(frozen=True)
class RunTemperatureExportConfig:
    """Validated configuration for locating and inspecting a CAEN run."""

    run_root: Path
    run_root_source: str
    run_directory_pattern: str
    info_filename_pattern: str
    output_filename_pattern: str
    run_time_zone: str
    histogram_bin_width_seconds: float
    archiver: ArchiverSettings
    pvs: tuple[str, ...]


@dataclass(frozen=True)
class CaenRunMetadata:
    """Run identity and timezone-aware start and stop timestamps."""

    run_id: str
    start_time: datetime
    stop_time: datetime

    @property
    def duration(self) -> timedelta:
        """Return the elapsed run duration."""
        return self.stop_time.astimezone(timezone.utc) - self.start_time.astimezone(timezone.utc)


@dataclass(frozen=True)
class RunPaths:
    """Paths derived from one run ID and the configured patterns."""

    run_directory: Path
    info_file: Path
    output_file: Path


def _required_string(config: Mapping[str, object], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"Required string is missing or empty: {key}")
    return value.strip()


def _run_root_value(
    config: Mapping[str, object],
    cli_run_root: str | Path | None,
    environ: Mapping[str, str],
) -> tuple[Path, str]:
    if cli_run_root is not None:
        raw_value = str(cli_run_root).strip()
        source = "CLI"
    elif environ.get(RUN_ROOT_ENV, "").strip():
        raw_value = environ[RUN_ROOT_ENV].strip()
        source = "environment"
    else:
        raw_config_value = config.get("run_root")
        raw_value = raw_config_value.strip() if isinstance(raw_config_value, str) else ""
        source = "JSON"

    if not raw_value:
        raise ConfigurationError(
            "run_root is missing or empty after applying --run-root, "
            f"{RUN_ROOT_ENV}, and JSON configuration precedence"
        )
    return Path(raw_value).expanduser(), source


def _validate_time_zone(name: str) -> None:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigurationError(f"Invalid run_time_zone: {name}") from exc


def load_run_temperature_export_config(
    path: str | Path,
    *,
    cli_run_root: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> RunTemperatureExportConfig:
    """Load and validate exporter configuration without checking run paths."""
    raw = load_json(path)
    effective_environ = os.environ if environ is None else environ
    run_root, run_root_source = _run_root_value(raw, cli_run_root, effective_environ)

    run_time_zone = _required_string(raw, "run_time_zone")
    _validate_time_zone(run_time_zone)

    raw_bin_width = raw.get(
        "histogram_bin_width_seconds",
        DEFAULT_HISTOGRAM_BIN_WIDTH_SECONDS,
    )
    if isinstance(raw_bin_width, bool) or not isinstance(raw_bin_width, (int, float)):
        raise ConfigurationError(
            "histogram_bin_width_seconds must be a finite number greater than zero; "
            "booleans and strings are not accepted"
        )
    histogram_bin_width_seconds = float(raw_bin_width)
    if not math.isfinite(histogram_bin_width_seconds) or histogram_bin_width_seconds <= 0:
        raise ConfigurationError(
            "histogram_bin_width_seconds must be a finite number greater than zero"
        )

    raw_archiver = require_mapping(raw, "archiver")
    retrieval_url = _required_string(raw_archiver, "retrieval_url")
    try:
        timeout_seconds = float(raw_archiver.get("timeout_seconds", 10.0))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("archiver.timeout_seconds must be a number") from exc
    if timeout_seconds <= 0:
        raise ConfigurationError("archiver.timeout_seconds must be positive")

    raw_pvs = require_list(raw, "pvs")
    if not raw_pvs or not all(isinstance(pv, str) and pv.strip() for pv in raw_pvs):
        raise ConfigurationError("pvs must be a non-empty list of non-empty strings")

    return RunTemperatureExportConfig(
        run_root=run_root,
        run_root_source=run_root_source,
        run_directory_pattern=_required_string(raw, "run_directory_pattern"),
        info_filename_pattern=_required_string(raw, "info_filename_pattern"),
        output_filename_pattern=_required_string(raw, "output_filename_pattern"),
        run_time_zone=run_time_zone,
        histogram_bin_width_seconds=histogram_bin_width_seconds,
        archiver=ArchiverSettings(
            retrieval_url=retrieval_url,
            timeout_seconds=timeout_seconds,
        ),
        pvs=tuple(pv.strip() for pv in raw_pvs),
    )


def format_run_pattern(pattern: str, run_id: str, *, setting_name: str) -> str:
    """Format a path pattern that may contain only an unmodified ``run_id`` field."""
    validate_run_id(run_id)
    try:
        fields = list(Formatter().parse(pattern))
    except ValueError as exc:
        raise ConfigurationError(f"Invalid {setting_name}: {exc}") from exc

    for _, field_name, format_spec, conversion in fields:
        if field_name is None:
            continue
        if field_name != "run_id":
            raise ConfigurationError(
                f"Unknown field in {setting_name}: {{{field_name}}}; only {{run_id}} is supported"
            )
        if format_spec or conversion:
            raise ConfigurationError(
                f"Formatting and conversion are not supported in {setting_name}: {pattern}"
            )

    return pattern.format(run_id=run_id)


def validate_run_id(run_id: str) -> None:
    """Reject run identifiers that could be interpreted as filesystem traversal."""
    if not run_id:
        raise RunTemperatureExportError("Run ID must not be empty")
    if ".." in run_id or "/" in run_id or "\\" in run_id or "\x00" in run_id:
        raise RunTemperatureExportError(
            "Run ID must not contain path separators, null bytes, or '..'"
        )


def _contained_path(base: Path, relative_path: str, *, setting_name: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ConfigurationError(f"{setting_name} must be a relative path: {relative}")
    try:
        resolved_base = base.resolve(strict=False)
        resolved_candidate = (resolved_base / relative).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ConfigurationError(f"Cannot resolve {setting_name}: {relative}: {exc}") from exc
    if not resolved_candidate.is_relative_to(resolved_base):
        raise ConfigurationError(
            f"{setting_name} resolves outside its allowed directory: {resolved_candidate}"
        )
    return resolved_candidate


def _resolve_pattern_path(base: Path, pattern: str, run_id: str, setting_name: str) -> Path:
    relative_path = format_run_pattern(pattern, run_id, setting_name=setting_name)
    return _contained_path(base, relative_path, setting_name=setting_name)


def resolve_run_directory(config: RunTemperatureExportConfig, run_id: str) -> Path:
    """Resolve a run directory and ensure that it remains below ``run_root``."""
    return _resolve_pattern_path(
        config.run_root,
        config.run_directory_pattern,
        run_id,
        "run_directory_pattern",
    )


def resolve_info_file(
    config: RunTemperatureExportConfig,
    run_id: str,
) -> Path:
    """Resolve the CAEN metadata file below the selected run directory."""
    directory = resolve_run_directory(config, run_id)
    return _resolve_pattern_path(
        directory,
        config.info_filename_pattern,
        run_id,
        "info_filename_pattern",
    )


def resolve_output_file(
    config: RunTemperatureExportConfig,
    run_id: str,
) -> Path:
    """Resolve the future ROOT output path without creating it."""
    directory = resolve_run_directory(config, run_id)
    return _resolve_pattern_path(
        directory,
        config.output_filename_pattern,
        run_id,
        "output_filename_pattern",
    )


def resolve_run_paths(config: RunTemperatureExportConfig, run_id: str) -> RunPaths:
    """Resolve all paths needed by the dry-run inspection."""
    run_directory = resolve_run_directory(config, run_id)
    return RunPaths(
        run_directory=run_directory,
        info_file=_resolve_pattern_path(
            run_directory,
            config.info_filename_pattern,
            run_id,
            "info_filename_pattern",
        ),
        output_file=_resolve_pattern_path(
            run_directory,
            config.output_filename_pattern,
            run_id,
            "output_filename_pattern",
        ),
    )


def require_run_directory(path: Path) -> None:
    """Require an existing directory for a concrete CAEN run."""
    if not path.exists():
        raise RunTemperatureExportError(f"Run directory does not exist: {path}")
    if not path.is_dir():
        raise RunTemperatureExportError(f"Run directory is not a directory: {path}")


def _metadata_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    known_labels = {"Run ID", "Start time", "Stop time"}
    for line in text.splitlines():
        if "=" not in line:
            continue
        label, value = line.split("=", 1)
        label = label.strip()
        if label in known_labels:
            if label in fields:
                raise RunTemperatureExportError(f"Duplicate {label} in CAEN metadata file")
            fields[label] = value.strip()
    return fields


def _localize_caen_time(naive: datetime, zone: ZoneInfo, *, field_name: str) -> datetime:
    candidates = [naive.replace(tzinfo=zone, fold=fold) for fold in (0, 1)]
    valid = [
        candidate
        for candidate in candidates
        if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive
    ]
    if not valid:
        raise RunTemperatureExportError(
            f"Nonexistent local {field_name} during a DST transition: {naive.isoformat()}"
        )
    if len(valid) == 2 and valid[0].utcoffset() != valid[1].utcoffset():
        raise RunTemperatureExportError(
            f"Ambiguous local {field_name} during a DST transition: {naive.isoformat()}"
        )
    return valid[0]


def parse_caen_timestamp(value: str, time_zone: str, *, field_name: str) -> datetime:
    """Parse one CAEN timestamp with an Italian month abbreviation."""
    try:
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RunTemperatureExportError(f"Invalid timezone: {time_zone}") from exc

    match = CAEN_TIMESTAMP_RE.fullmatch(value)
    if match is None:
        raise RunTemperatureExportError(
            f"Invalid {field_name} format: {value!r}; expected '<weekday> <Italian month> "
            "<day> HH:MM:SS <year>'"
        )

    month_name = match.group("month").lower()
    try:
        month = ITALIAN_MONTHS[month_name]
    except KeyError as exc:
        raise RunTemperatureExportError(
            f"Invalid Italian month abbreviation in {field_name}: {match.group('month')}"
        ) from exc

    try:
        naive = datetime(
            year=int(match.group("year")),
            month=month,
            day=int(match.group("day")),
            hour=int(match.group("hour")),
            minute=int(match.group("minute")),
            second=int(match.group("second")),
        )
    except ValueError as exc:
        raise RunTemperatureExportError(
            f"Invalid {field_name} value: {value!r}: {exc}"
        ) from exc
    return _localize_caen_time(naive, zone, field_name=field_name)


def parse_caen_run_info(
    path: str | Path,
    requested_run_id: str,
    time_zone: str,
) -> CaenRunMetadata:
    """Read and validate a completed CAEN run metadata file."""
    validate_run_id(requested_run_id)
    info_path = Path(path)
    if not info_path.exists():
        raise RunTemperatureExportError(f"CAEN run metadata file does not exist: {info_path}")
    if not info_path.is_file():
        raise RunTemperatureExportError(f"CAEN run metadata path is not a file: {info_path}")

    try:
        fields = _metadata_fields(info_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RunTemperatureExportError(
            f"Cannot read CAEN run metadata file {info_path}: {exc}"
        ) from exc

    file_run_id = fields.get("Run ID", "")
    if not file_run_id:
        raise RunTemperatureExportError(f"Run ID is missing in CAEN metadata file: {info_path}")
    if file_run_id != requested_run_id:
        raise RunTemperatureExportError(
            f"Run ID mismatch in {info_path}: requested {requested_run_id!r}, "
            f"found {file_run_id!r}"
        )

    start_value = fields.get("Start time", "")
    if not start_value:
        raise RunTemperatureExportError(f"Start time is missing in CAEN metadata file: {info_path}")
    stop_value = fields.get("Stop time", "")
    if not stop_value:
        raise RunTemperatureExportError(
            f"Stop time is missing in CAEN metadata file: {info_path}; active runs are not supported"
        )

    start_time = parse_caen_timestamp(start_value, time_zone, field_name="start time")
    stop_time = parse_caen_timestamp(stop_value, time_zone, field_name="stop time")
    if stop_time.astimezone(timezone.utc) < start_time.astimezone(timezone.utc):
        raise RunTemperatureExportError(
            f"Stop time {stop_time.isoformat()} is before start time {start_time.isoformat()}"
        )

    return CaenRunMetadata(
        run_id=file_run_id,
        start_time=start_time,
        stop_time=stop_time,
    )


def _format_duration(duration: timedelta) -> str:
    total_seconds = int(duration.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d} ({total_seconds} seconds)"


def format_dry_run_summary(
    config: RunTemperatureExportConfig,
    paths: RunPaths,
    metadata: CaenRunMetadata,
) -> str:
    """Build the operator-readable dry-run report."""
    lines = [
        "CAEN run temperature export dry-run",
        f"Run ID: {metadata.run_id}",
        f"run_root source: {config.run_root_source}",
        f"Run directory: {paths.run_directory}",
        f"Metadata file: {paths.info_file}",
        f"Start local: {metadata.start_time.isoformat()}",
        f"Stop local: {metadata.stop_time.isoformat()}",
        f"Start UTC: {metadata.start_time.astimezone(timezone.utc).isoformat()}",
        f"Stop UTC: {metadata.stop_time.astimezone(timezone.utc).isoformat()}",
        f"Duration: {_format_duration(metadata.duration)}",
        f"Future ROOT output: {paths.output_file}",
        f"Histogram bin width: {config.histogram_bin_width_seconds:g} seconds",
        "Configured temperature PVs:",
    ]
    lines.extend(f"  - {pv}" for pv in config.pvs)
    return "\n".join(lines)


def main(
    argv: list[str] | None = None,
    *,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Run the metadata-only temperature export dry-run command."""
    parser = argparse.ArgumentParser(prog="bdx_run_temperature_export")
    parser.add_argument("run_id", metavar="RUN_ID", help="Opaque CAEN run identifier")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"JSON configuration file (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument("--run-root", help=f"Override run_root (also: {RUN_ROOT_ENV})")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse metadata and print planned paths without writing anything",
    )
    args = parser.parse_args(argv)

    output = output or sys.stdout
    error_output = error_output or sys.stderr
    if not args.dry_run:
        print(
            "Error: --dry-run is required because temperature retrieval and ROOT output "
            "are not implemented yet",
            file=error_output,
        )
        return 2

    try:
        config = load_run_temperature_export_config(
            args.config,
            cli_run_root=args.run_root,
            environ=environ,
        )
        paths = resolve_run_paths(config, args.run_id)
        require_run_directory(paths.run_directory)
        metadata = parse_caen_run_info(paths.info_file, args.run_id, config.run_time_zone)
    except (ConfigurationError, RunTemperatureExportError, OSError) as exc:
        print(f"Error: {exc}", file=error_output)
        return 2

    print(format_dry_run_summary(config, paths, metadata), file=output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
