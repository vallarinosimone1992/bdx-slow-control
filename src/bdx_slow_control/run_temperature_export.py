"""Parse CAEN run metadata, query temperatures, and export run files."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
from string import Formatter
import sys
import tempfile
from typing import Callable, Mapping, TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .archiver_retrieval import (
    ArchivedSample,
    ArchiverRetrievalError,
    query_pvs,
    summarize_samples,
)
from .config import ConfigurationError, load_json, require_list, require_mapping
from .root_temperature_writer import (
    MISSING_ALARM_VALUE,
    RootTemperatureWriterError,
    RootWriteResult,
    write_root_file,
)


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
    """Archiver endpoint and retrieval timeout."""

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
    missing_alarm_value: int
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

    missing_alarm_value = raw.get("missing_alarm_value", MISSING_ALARM_VALUE)
    if isinstance(missing_alarm_value, bool) or not isinstance(missing_alarm_value, int):
        raise ConfigurationError("missing_alarm_value must be an int32 integer")
    if not -(2**31) <= missing_alarm_value < 2**31:
        raise ConfigurationError("missing_alarm_value must fit int32")

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
        missing_alarm_value=missing_alarm_value,
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
    """Resolve the ROOT output path without creating it."""
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
    *,
    heading: str = "CAEN run temperature export dry-run",
) -> str:
    """Build the operator-readable run report."""
    lines = [
        heading,
        f"Run ID: {metadata.run_id}",
        f"run_root source: {config.run_root_source}",
        f"Run directory: {paths.run_directory}",
        f"Metadata file: {paths.info_file}",
        f"Start local: {metadata.start_time.isoformat()}",
        f"Stop local: {metadata.stop_time.isoformat()}",
        f"Start UTC: {metadata.start_time.astimezone(timezone.utc).isoformat()}",
        f"Stop UTC: {metadata.stop_time.astimezone(timezone.utc).isoformat()}",
        f"Duration: {_format_duration(metadata.duration)}",
        f"ROOT output: {paths.output_file}",
        f"Histogram bin width: {config.histogram_bin_width_seconds:g} seconds",
        "Configured temperature PVs:",
    ]
    lines.extend(f"  - {pv}" for pv in config.pvs)
    return "\n".join(lines)


def _pv_short_name(pv: str) -> str:
    fields = pv.split(":")
    return fields[-2] if len(fields) >= 2 else pv


def build_json_dump(
    config: RunTemperatureExportConfig,
    metadata: CaenRunMetadata,
    samples_by_pv: Mapping[str, list[ArchivedSample]],
) -> dict[str, object]:
    """Build the normalized diagnostic JSON document."""
    return {
        "run": {
            "run_id": metadata.run_id,
            "start_local": metadata.start_time.isoformat(),
            "stop_local": metadata.stop_time.isoformat(),
            "start_utc": metadata.start_time.astimezone(timezone.utc).isoformat(),
            "stop_utc": metadata.stop_time.astimezone(timezone.utc).isoformat(),
            "duration_seconds": int(metadata.duration.total_seconds()),
        },
        "archiver": {
            "retrieval_url": config.archiver.retrieval_url,
            "timeout_seconds": config.archiver.timeout_seconds,
        },
        "configuration": {
            "run_root": str(config.run_root),
            "run_time_zone": config.run_time_zone,
            "histogram_bin_width_seconds": config.histogram_bin_width_seconds,
            "missing_alarm_value": config.missing_alarm_value,
            "pvs": list(config.pvs),
        },
        "pvs": {
            pv: {
                "name": _pv_short_name(pv),
                **summarize_samples(samples_by_pv[pv]),
                "samples": [sample.as_dict() for sample in samples_by_pv[pv]],
            }
            for pv in config.pvs
        },
    }


def write_json_dump(
    path: str | Path,
    payload: Mapping[str, object],
    *,
    overwrite: bool,
) -> Path:
    """Atomically publish a diagnostic JSON dump without creating parent directories."""
    requested = Path(path).expanduser()
    parent = requested.parent.resolve(strict=False)
    target = parent / requested.name
    if not parent.is_dir():
        raise RunTemperatureExportError(f"JSON dump directory does not exist: {parent}")
    if target.exists() and not overwrite:
        raise RunTemperatureExportError(
            f"JSON dump already exists: {target}; use --overwrite to replace it"
        )

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        if overwrite:
            os.replace(temporary_path, target)
        else:
            try:
                os.link(temporary_path, target)
            except FileExistsError as exc:
                raise RunTemperatureExportError(
                    f"JSON dump already exists: {target}; use --overwrite to replace it"
                ) from exc
            temporary_path.unlink()
    finally:
        temporary_path.unlink(missing_ok=True)
    return target


QueryArchiver = Callable[
    [str, tuple[str, ...], datetime, datetime, float],
    dict[str, list[ArchivedSample]],
]

WriteRoot = Callable[..., RootWriteResult]


def _format_archiver_summaries(
    config: RunTemperatureExportConfig,
    samples_by_pv: Mapping[str, list[ArchivedSample]],
) -> str:
    lines = ["Archiver retrieval summary:"]
    pvs_with_samples = 0
    total_samples = 0
    for pv in config.pvs:
        if pv not in samples_by_pv:
            raise RunTemperatureExportError(f"Archiver result is missing configured PV: {pv}")
        summary = summarize_samples(samples_by_pv[pv])
        sample_count = summary["sample_count"]
        total_samples += sample_count
        lines.extend([f"{pv}:", f"  samples: {sample_count}"])
        if summary["warning"]:
            lines.append(f"  warning: {summary['warning']}")
            continue
        pvs_with_samples += 1
        lines.extend(
            [
                f"  first timestamp: {summary['first_timestamp']}",
                f"  last timestamp: {summary['last_timestamp']}",
                f"  minimum: {summary['minimum']:g}",
                f"  maximum: {summary['maximum']:g}",
                "  samples with non-nominal or unavailable status/severity: "
                f"{summary['alarm_or_unavailable_count']}",
            ]
        )
    lines.extend(
        [
            f"Configured PVs: {len(config.pvs)}",
            f"PVs with samples: {pvs_with_samples}",
            f"Empty PVs: {len(config.pvs) - pvs_with_samples}",
            f"Total samples: {total_samples}",
        ]
    )
    return "\n".join(lines)


def main(
    argv: list[str] | None = None,
    *,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
    query_archiver: QueryArchiver = query_pvs,
    write_root: WriteRoot = write_root_file,
) -> int:
    """Inspect a run and optionally retrieve its archived temperature samples."""
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
    parser.add_argument(
        "--query-archiver",
        action="store_true",
        help="Retrieve and summarize configured PV samples without writing ROOT output",
    )
    parser.add_argument(
        "--write-root",
        action="store_true",
        help="Retrieve configured PV samples and atomically write the final ROOT file",
    )
    parser.add_argument("--dump-json", help="Optional path for a normalized diagnostic dump")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacement of existing JSON and ROOT output files",
    )
    args = parser.parse_args(argv)

    output = output or sys.stdout
    error_output = error_output or sys.stderr
    network_mode = args.query_archiver or args.write_root
    if args.dry_run and network_mode:
        print(
            "Error: choose either --dry-run or a network mode "
            "(--query-archiver/--write-root)",
            file=error_output,
        )
        return 2
    if not args.dry_run and not network_mode:
        print(
            "Error: one mode is required: --dry-run, --query-archiver, or --write-root",
            file=error_output,
        )
        return 2
    if args.dump_json and not network_mode:
        print(
            "Error: --dump-json requires --query-archiver or --write-root",
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

    if args.write_root:
        heading = "CAEN run temperature ROOT export"
    elif args.query_archiver:
        heading = "CAEN run temperature Archiver query"
    else:
        heading = "CAEN run temperature export dry-run"
    print(format_dry_run_summary(config, paths, metadata, heading=heading), file=output)
    if not network_mode:
        return 0

    try:
        samples_by_pv = query_archiver(
            config.archiver.retrieval_url,
            config.pvs,
            metadata.start_time.astimezone(timezone.utc),
            metadata.stop_time.astimezone(timezone.utc),
            config.archiver.timeout_seconds,
        )
        print(_format_archiver_summaries(config, samples_by_pv), file=output)
        if args.write_root:
            result = write_root(
                paths.output_file,
                run_id=metadata.run_id,
                start_local=metadata.start_time,
                stop_local=metadata.stop_time,
                caen_time_zone=config.run_time_zone,
                bin_width_seconds=config.histogram_bin_width_seconds,
                pvs=config.pvs,
                samples_by_pv=samples_by_pv,
                archiver_endpoint=config.archiver.retrieval_url,
                missing_alarm_value=config.missing_alarm_value,
                overwrite=args.overwrite,
            )
            print(
                "\n".join(
                    [
                        f"ROOT file: {result.path}",
                        f"TTree entries: {result.tree_entries}",
                        f"Histogram bins: {result.histogram_bins}",
                        f"Histogram bin width: {result.bin_width_seconds:g} seconds",
                        "Sensors with data: "
                        + (", ".join(result.sensors_with_data) or "none"),
                        "Empty sensors: " + (", ".join(result.empty_sensors) or "none"),
                        f"ROOT file size: {result.file_size_bytes} bytes",
                    ]
                ),
                file=output,
            )
        if args.dump_json:
            dump_path = write_json_dump(
                args.dump_json,
                build_json_dump(config, metadata, samples_by_pv),
                overwrite=args.overwrite,
            )
            print(f"Diagnostic JSON dump: {dump_path}", file=output)
    except (
        ArchiverRetrievalError,
        RootTemperatureWriterError,
        RunTemperatureExportError,
        OSError,
        ValueError,
    ) as exc:
        print(f"Error: {exc}", file=error_output)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
