"""Build and atomically publish the run-temperature ROOT file.

The metadata are stored as UTF-8 JSON in a ROOT ``TObjString`` named
``metadata``.  This keeps the complete sensor mapping and scalar provenance in
one object that is directly readable as ``str(root_file["metadata"])`` with
uproot and as a normal ``TObjString`` with CERN ROOT.

NumPy and uproot are optional dependencies.  They are imported only when a
ROOT-export function is called, so the rest of the slow-control package remains
usable without the ``export`` extra.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import json
import math
import numbers
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping

from . import __version__
from .archiver_retrieval import ArchivedSample, datetime_to_timestamp_ns


TREE_NAME = "temperature_samples"
METADATA_NAME = "metadata"
METADATA_FORMAT = "bdx-run-temperature-export-metadata-v1"
MISSING_ALARM_VALUE = -1
TREE_SCHEMA = {
    "sensor_index": "int32",
    "seconds": "int64",
    "nanoseconds": "int32",
    "timestamp_ns": "int64",
    "time_from_run_start_s": "float64",
    "value": "float64",
    "status": "int32",
    "severity": "int32",
}
EPICS_ALARM_STATUS_CODES = {
    "NO_ALARM": 0,
    "READ_ALARM": 1,
    "WRITE_ALARM": 2,
    "HIHI_ALARM": 3,
    "HIGH_ALARM": 4,
    "LOLO_ALARM": 5,
    "LOW_ALARM": 6,
    "STATE_ALARM": 7,
    "COS_ALARM": 8,
    "COMM_ALARM": 9,
    "TIMEOUT_ALARM": 10,
    "HW_LIMIT_ALARM": 11,
    "CALC_ALARM": 12,
    "SCAN_ALARM": 13,
    "LINK_ALARM": 14,
    "SOFT_ALARM": 15,
    "BAD_SUB_ALARM": 16,
    "UDF_ALARM": 17,
    "DISABLE_ALARM": 18,
    "SIMM_ALARM": 19,
    "READ_ACCESS_ALARM": 20,
    "WRITE_ACCESS_ALARM": 21,
}
EPICS_ALARM_SEVERITY_CODES = {
    "NO_ALARM": 0,
    "MINOR_ALARM": 1,
    "MAJOR_ALARM": 2,
    "INVALID_ALARM": 3,
}


class RootTemperatureWriterError(RuntimeError):
    """Raised when ROOT export preparation, writing, or validation fails."""


class RootExportDependencyError(RootTemperatureWriterError):
    """Raised when the optional ROOT export dependencies are unavailable."""


@dataclass(frozen=True)
class HistogramData:
    """ROOT-independent numerical representation of one TH1D."""

    name: str
    title: str
    bin_edges: Any
    contents: Any
    errors: Any
    sample_count: int


@dataclass(frozen=True)
class RootWriteResult:
    """Operator-facing facts about a successfully published ROOT file."""

    path: Path
    tree_entries: int
    histogram_bins: int
    bin_width_seconds: float
    sensors_with_data: tuple[str, ...]
    empty_sensors: tuple[str, ...]
    file_size_bytes: int


def _load_dependencies() -> tuple[Any, Any]:
    missing: list[str] = []
    modules: dict[str, Any] = {}
    for name in ("numpy", "uproot"):
        try:
            modules[name] = importlib.import_module(name)
        except ImportError:
            missing.append(name)
    if missing:
        raise RootExportDependencyError(
            "ROOT export requires the optional NumPy and uproot dependencies; "
            "install the package with the 'export' extra "
            f"(missing: {', '.join(missing)})"
        )
    return modules["numpy"], modules["uproot"]


def _require_aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RootTemperatureWriterError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def sensor_short_name(pv: str) -> str:
    """Extract the configured sensor name, such as ``T00``, from a PV."""
    fields = pv.split(":")
    return fields[-2] if len(fields) >= 2 else pv


def _validate_sensor_configuration(pvs: tuple[str, ...]) -> tuple[str, ...]:
    if not pvs:
        raise RootTemperatureWriterError("At least one temperature PV must be configured")
    short_names = tuple(sensor_short_name(pv) for pv in pvs)
    if len(set(short_names)) != len(short_names):
        raise RootTemperatureWriterError(
            "Configured temperature PVs produce duplicate sensor short names"
        )
    return short_names


def _validate_samples_mapping(
    pvs: tuple[str, ...],
    samples_by_pv: Mapping[str, list[ArchivedSample]],
) -> None:
    missing = [pv for pv in pvs if pv not in samples_by_pv]
    if missing:
        raise RootTemperatureWriterError(
            "Archiver result is missing configured PVs: " + ", ".join(missing)
        )


def _alarm_value_as_int32(
    value: Any,
    *,
    sentinel: int,
    field_name: str,
    sample: ArchivedSample,
) -> int:
    if value is None:
        return sentinel
    if isinstance(value, str):
        label = value.strip().upper().replace(" ", "_")
        mapping = (
            EPICS_ALARM_STATUS_CODES
            if field_name == "status"
            else EPICS_ALARM_SEVERITY_CODES
        )
        if label in mapping:
            return mapping[label]
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise RootTemperatureWriterError(
            f"Sample {field_name} for {sample.pv} at {sample.timestamp_utc} "
            "must be an integer or missing"
        )
    normalized = int(value)
    if not -(2**31) <= normalized < 2**31:
        raise RootTemperatureWriterError(
            f"Sample {field_name} for {sample.pv} at {sample.timestamp_utc} "
            "does not fit int32"
        )
    return normalized


def prepare_tree_arrays(
    pvs: tuple[str, ...],
    samples_by_pv: Mapping[str, list[ArchivedSample]],
    start_utc: datetime,
    *,
    missing_alarm_value: int = MISSING_ALARM_VALUE,
) -> dict[str, Any]:
    """Prepare lossless, sorted NumPy arrays for ``temperature_samples``."""
    np, _ = _load_dependencies()
    _validate_sensor_configuration(pvs)
    _validate_samples_mapping(pvs, samples_by_pv)
    if isinstance(missing_alarm_value, bool) or not isinstance(
        missing_alarm_value, numbers.Integral
    ):
        raise RootTemperatureWriterError("missing_alarm_value must be an int32 integer")
    missing_alarm_value = int(missing_alarm_value)
    if not -(2**31) <= missing_alarm_value < 2**31:
        raise RootTemperatureWriterError("missing_alarm_value must fit int32")

    start_ns = datetime_to_timestamp_ns(_require_aware_utc(start_utc, "start_utc"))
    rows: list[tuple[int, int, ArchivedSample]] = []
    for sensor_index, pv in enumerate(pvs):
        for sample in samples_by_pv[pv]:
            if sample.pv != pv:
                raise RootTemperatureWriterError(
                    f"Archiver sample is assigned to {pv} but names {sample.pv}"
                )
            rows.append((sample.timestamp_ns, sensor_index, sample))
    rows.sort(key=lambda row: (row[0], row[1]))

    return {
        "sensor_index": np.asarray([row[1] for row in rows], dtype=np.int32),
        "seconds": np.asarray([row[2].seconds for row in rows], dtype=np.int64),
        "nanoseconds": np.asarray(
            [row[2].nanoseconds for row in rows], dtype=np.int32
        ),
        "timestamp_ns": np.asarray([row[2].timestamp_ns for row in rows], dtype=np.int64),
        "time_from_run_start_s": np.asarray(
            [(row[2].timestamp_ns - start_ns) / 1_000_000_000 for row in rows],
            dtype=np.float64,
        ),
        "value": np.asarray([row[2].value for row in rows], dtype=np.float64),
        "status": np.asarray(
            [
                _alarm_value_as_int32(
                    row[2].status,
                    sentinel=missing_alarm_value,
                    field_name="status",
                    sample=row[2],
                )
                for row in rows
            ],
            dtype=np.int32,
        ),
        "severity": np.asarray(
            [
                _alarm_value_as_int32(
                    row[2].severity,
                    sentinel=missing_alarm_value,
                    field_name="severity",
                    sample=row[2],
                )
                for row in rows
            ],
            dtype=np.int32,
        ),
    }


def _histogram_bin_edges(duration_seconds: float, bin_width_seconds: float, np: Any) -> Any:
    if not math.isfinite(duration_seconds) or duration_seconds < 0:
        raise RootTemperatureWriterError("Run duration must be finite and non-negative")
    if not math.isfinite(bin_width_seconds) or bin_width_seconds <= 0:
        raise RootTemperatureWriterError("Histogram bin width must be finite and positive")
    bin_count = max(1, math.ceil(duration_seconds / bin_width_seconds))
    return np.arange(bin_count + 1, dtype=np.float64) * bin_width_seconds


def build_histograms(
    pvs: tuple[str, ...],
    samples_by_pv: Mapping[str, list[ArchivedSample]],
    start_utc: datetime,
    stop_utc: datetime,
    bin_width_seconds: float,
) -> dict[str, HistogramData]:
    """Build mean, SEM, and count arrays without interpolation or filling."""
    np, _ = _load_dependencies()
    short_names = _validate_sensor_configuration(pvs)
    _validate_samples_mapping(pvs, samples_by_pv)
    start = _require_aware_utc(start_utc, "start_utc")
    stop = _require_aware_utc(stop_utc, "stop_utc")
    duration_seconds = (stop - start).total_seconds()
    bin_edges = _histogram_bin_edges(duration_seconds, bin_width_seconds, np)
    bin_count = len(bin_edges) - 1
    start_ns = datetime_to_timestamp_ns(start)

    histograms: dict[str, HistogramData] = {}
    for pv, short_name in zip(pvs, short_names):
        samples = samples_by_pv[pv]
        grouped_values: list[list[float]] = [[] for _ in range(bin_count)]
        for sample in samples:
            offset = (sample.timestamp_ns - start_ns) / 1_000_000_000
            if offset < 0 or offset > duration_seconds:
                raise RootTemperatureWriterError(
                    f"Sample for {pv} at {sample.timestamp_utc} is outside the run interval"
                )
            bin_index = int(np.searchsorted(bin_edges, offset, side="right") - 1)
            if bin_index == bin_count:
                bin_index = bin_count - 1
            grouped_values[bin_index].append(sample.value)

        counts = np.asarray([len(values) for values in grouped_values], dtype=np.float64)
        means = np.zeros(bin_count, dtype=np.float64)
        errors = np.zeros(bin_count, dtype=np.float64)
        for bin_index, values in enumerate(grouped_values):
            if not values:
                continue
            values_array = np.asarray(values, dtype=np.float64)
            means[bin_index] = float(np.mean(values_array))
            if len(values) >= 2:
                errors[bin_index] = float(
                    np.std(values_array, ddof=1) / math.sqrt(len(values))
                )

        temperature_name = f"temperature_{short_name}"
        counts_name = f"{temperature_name}_counts"
        histograms[temperature_name] = HistogramData(
            name=temperature_name,
            title=f"{short_name} mean temperature;Time from run start [s];Temperature",
            bin_edges=bin_edges.copy(),
            contents=means,
            errors=errors,
            sample_count=len(samples),
        )
        histograms[counts_name] = HistogramData(
            name=counts_name,
            title=f"{short_name} samples per bin;Time from run start [s];Samples",
            bin_edges=bin_edges.copy(),
            contents=counts,
            errors=np.zeros(bin_count, dtype=np.float64),
            sample_count=len(samples),
        )
    return histograms


def discover_git_commit() -> str | None:
    """Return the package checkout commit when Git is available, otherwise ``None``."""
    checkout = Path(__file__).resolve().parents[2]
    try:
        completed = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = completed.stdout.strip()
    return commit if completed.returncode == 0 and commit else None


def build_metadata(
    *,
    run_id: str,
    start_local: datetime,
    stop_local: datetime,
    caen_time_zone: str,
    bin_width_seconds: float,
    pvs: tuple[str, ...],
    samples_by_pv: Mapping[str, list[ArchivedSample]],
    archiver_endpoint: str,
    missing_alarm_value: int = MISSING_ALARM_VALUE,
    generated_at_utc: datetime | None = None,
    package_version: str = __version__,
    git_commit: str | None = None,
) -> dict[str, Any]:
    """Build the JSON-serializable metadata document stored in ``metadata``."""
    short_names = _validate_sensor_configuration(pvs)
    _validate_samples_mapping(pvs, samples_by_pv)
    start_utc = _require_aware_utc(start_local, "start_local")
    stop_utc = _require_aware_utc(stop_local, "stop_local")
    generated = _require_aware_utc(
        generated_at_utc or datetime.now(timezone.utc), "generated_at_utc"
    )
    return {
        "format": METADATA_FORMAT,
        "storage": "UTF-8 JSON serialized in ROOT TObjString 'metadata'",
        "run_id": run_id,
        "start_local": start_local.isoformat(),
        "stop_local": stop_local.isoformat(),
        "start_utc": start_utc.isoformat(),
        "stop_utc": stop_utc.isoformat(),
        "duration_seconds": (stop_utc - start_utc).total_seconds(),
        "caen_time_zone": caen_time_zone,
        "histogram_bin_width_seconds": bin_width_seconds,
        "configured_pv_count": len(pvs),
        "total_sample_count": sum(len(samples_by_pv[pv]) for pv in pvs),
        "missing_status_severity_sentinel": missing_alarm_value,
        "alarm_field_encoding": (
            "int32 EPICS alarm codes; recognized named enum values are converted "
            "to their canonical integer codes"
        ),
        "sensors": [
            {
                "sensor_index": sensor_index,
                "short_name": short_name,
                "pv": pv,
                "sample_count": len(samples_by_pv[pv]),
            }
            for sensor_index, (short_name, pv) in enumerate(zip(short_names, pvs))
        ],
        "archiver_endpoint": archiver_endpoint,
        "generated_at_utc": generated.isoformat(),
        "package_version": package_version,
        "git_commit": git_commit,
    }


def _to_root_histogram(histogram: HistogramData, np: Any, uproot: Any) -> Any:
    bin_count = len(histogram.contents)
    data = np.zeros(bin_count + 2, dtype=np.float64)
    data[1:-1] = histogram.contents
    sumw2 = np.zeros(bin_count + 2, dtype=np.float64)
    sumw2[1:-1] = np.square(histogram.errors)
    centers = (histogram.bin_edges[:-1] + histogram.bin_edges[1:]) / 2
    total_weight = float(np.sum(histogram.contents))
    return uproot.writing.identify.to_TH1x(
        fName=None,
        fTitle=histogram.title,
        data=data,
        fEntries=float(histogram.sample_count),
        fTsumw=total_weight,
        fTsumw2=float(np.sum(np.square(histogram.contents))),
        fTsumwx=float(np.sum(histogram.contents * centers)),
        fTsumwx2=float(np.sum(histogram.contents * np.square(centers))),
        fSumw2=sumw2,
        fXaxis=uproot.writing.identify.to_TAxis(
            fName="xaxis",
            fTitle="Time from run start [s]",
            fNbins=bin_count,
            fXmin=float(histogram.bin_edges[0]),
            fXmax=float(histogram.bin_edges[-1]),
        ),
    )


def _write_temporary_file(
    path: Path,
    tree_arrays: Mapping[str, Any],
    histograms: Mapping[str, HistogramData],
    metadata: Mapping[str, Any],
    np: Any,
    uproot: Any,
) -> None:
    with uproot.recreate(path) as root_file:
        tree = root_file.mktree(TREE_NAME, TREE_SCHEMA)
        if len(tree_arrays["sensor_index"]):
            tree.extend(tree_arrays)
        for name, histogram in histograms.items():
            root_file[name] = _to_root_histogram(histogram, np, uproot)
        root_file[METADATA_NAME] = json.dumps(
            metadata, ensure_ascii=False, allow_nan=False, sort_keys=True
        )
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def validate_root_file(
    path: str | Path,
    *,
    expected_tree_entries: int,
    expected_histogram_names: tuple[str, ...],
) -> None:
    """Reopen a candidate file and validate its required physical ROOT objects."""
    np, uproot = _load_dependencies()
    candidate = Path(path)
    try:
        # Use uproot's direct local-file source: the fsspec source used by
        # uproot 5.7 for local paths can depend on an asyncio worker, which is
        # unnecessary for a same-directory validation read.
        with uproot.open(
            candidate,
            handler=uproot.source.file.MultithreadedFileSource,
        ) as root_file:
            keys = set(root_file.keys(cycle=False))
            if TREE_NAME not in keys:
                raise RootTemperatureWriterError(f"ROOT validation failed: missing {TREE_NAME}")
            tree = root_file[TREE_NAME]
            if tree.classname != "TTree":
                raise RootTemperatureWriterError(
                    "ROOT validation failed: temperature_samples is not a TTree "
                    f"(found {tree.classname})"
                )
            if tree.num_entries != expected_tree_entries:
                raise RootTemperatureWriterError(
                    "ROOT validation failed: temperature_samples entry count is "
                    f"{tree.num_entries}, expected {expected_tree_entries}"
                )
            for name in expected_histogram_names:
                if name not in keys:
                    raise RootTemperatureWriterError(
                        f"ROOT validation failed: missing histogram {name}"
                    )
                if root_file[name].classname != "TH1D":
                    raise RootTemperatureWriterError(
                        f"ROOT validation failed: {name} is not a TH1D"
                    )
            if METADATA_NAME not in keys:
                raise RootTemperatureWriterError("ROOT validation failed: missing metadata")
            if root_file[METADATA_NAME].classname != "TObjString":
                raise RootTemperatureWriterError(
                    "ROOT validation failed: metadata is not a TObjString"
                )
            parsed_metadata = json.loads(str(root_file[METADATA_NAME]))
            if not isinstance(parsed_metadata, dict):
                raise RootTemperatureWriterError(
                    "ROOT validation failed: metadata JSON is not an object"
                )
            if parsed_metadata.get("format") != METADATA_FORMAT:
                raise RootTemperatureWriterError(
                    "ROOT validation failed: metadata format is not "
                    f"{METADATA_FORMAT}"
                )

            total_sample_count = parsed_metadata.get("total_sample_count")
            if isinstance(total_sample_count, bool) or not isinstance(
                total_sample_count, int
            ):
                raise RootTemperatureWriterError(
                    "ROOT validation failed: metadata total_sample_count must be an integer"
                )
            if total_sample_count != tree.num_entries:
                raise RootTemperatureWriterError(
                    "ROOT validation failed: metadata total_sample_count "
                    f"{total_sample_count} does not match {TREE_NAME} entry count "
                    f"{tree.num_entries}"
                )

            sensors = parsed_metadata.get("sensors")
            if not isinstance(sensors, list):
                raise RootTemperatureWriterError(
                    "ROOT validation failed: metadata sensors must be a list"
                )
            sensor_sample_count = 0
            count_histogram_names: list[str] = []
            for sensor_index, sensor in enumerate(sensors):
                if not isinstance(sensor, dict):
                    raise RootTemperatureWriterError(
                        "ROOT validation failed: metadata sensor entries must be objects"
                    )
                sample_count = sensor.get("sample_count")
                if isinstance(sample_count, bool) or not isinstance(sample_count, int):
                    raise RootTemperatureWriterError(
                        "ROOT validation failed: metadata sensors["
                        f"{sensor_index}].sample_count must be an integer"
                    )
                short_name = sensor.get("short_name")
                if not isinstance(short_name, str) or not short_name:
                    raise RootTemperatureWriterError(
                        "ROOT validation failed: metadata sensors["
                        f"{sensor_index}].short_name must be a non-empty string"
                    )
                sensor_sample_count += sample_count
                count_histogram_names.append(f"temperature_{short_name}_counts")

            if sensor_sample_count != total_sample_count:
                raise RootTemperatureWriterError(
                    "ROOT validation failed: metadata total_sample_count "
                    f"{total_sample_count} does not match the sum of sensors sample_count "
                    f"{sensor_sample_count}"
                )

            if len(set(count_histogram_names)) != len(count_histogram_names):
                raise RootTemperatureWriterError(
                    "ROOT validation failed: metadata sensors produce duplicate "
                    "_counts histogram names"
                )
            histogram_sample_count = 0.0
            for name in count_histogram_names:
                if name not in keys:
                    raise RootTemperatureWriterError(
                        f"ROOT validation failed: missing histogram {name}"
                    )
                histogram = root_file[name]
                if histogram.classname != "TH1D":
                    raise RootTemperatureWriterError(
                        f"ROOT validation failed: {name} is not a TH1D"
                    )
                histogram_sample_count += float(
                    np.sum(histogram.values(flow=False), dtype=np.float64)
                )
            if (
                not math.isfinite(histogram_sample_count)
                or histogram_sample_count != total_sample_count
            ):
                raise RootTemperatureWriterError(
                    "ROOT validation failed: metadata total_sample_count "
                    f"{total_sample_count} does not match the sum of _counts histogram "
                    f"contents {histogram_sample_count}"
                )
    except RootTemperatureWriterError:
        raise
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise RootTemperatureWriterError(f"Cannot validate temporary ROOT file: {exc}") from exc


def write_root_file(
    path: str | Path,
    *,
    run_id: str,
    start_local: datetime,
    stop_local: datetime,
    caen_time_zone: str,
    bin_width_seconds: float,
    pvs: tuple[str, ...],
    samples_by_pv: Mapping[str, list[ArchivedSample]],
    archiver_endpoint: str,
    missing_alarm_value: int = MISSING_ALARM_VALUE,
    overwrite: bool = False,
) -> RootWriteResult:
    """Build, validate, and atomically publish the final ROOT file."""
    np, uproot = _load_dependencies()
    requested = Path(path).expanduser()
    parent = requested.parent.resolve(strict=False)
    target = parent / requested.name
    if not parent.is_dir():
        raise RootTemperatureWriterError(
            f"ROOT output directory does not exist: {parent}; it will not be created"
        )
    if target.exists() and not overwrite:
        raise RootTemperatureWriterError(
            f"ROOT output already exists: {target}; use --overwrite to replace it"
        )

    start_utc = _require_aware_utc(start_local, "start_local")
    stop_utc = _require_aware_utc(stop_local, "stop_local")
    tree_arrays = prepare_tree_arrays(
        pvs,
        samples_by_pv,
        start_utc,
        missing_alarm_value=missing_alarm_value,
    )
    histograms = build_histograms(
        pvs,
        samples_by_pv,
        start_utc,
        stop_utc,
        bin_width_seconds,
    )
    metadata = build_metadata(
        run_id=run_id,
        start_local=start_local,
        stop_local=stop_local,
        caen_time_zone=caen_time_zone,
        bin_width_seconds=bin_width_seconds,
        pvs=pvs,
        samples_by_pv=samples_by_pv,
        archiver_endpoint=archiver_endpoint,
        missing_alarm_value=missing_alarm_value,
        git_commit=discover_git_commit(),
    )

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=parent,
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    try:
        _write_temporary_file(
            temporary_path,
            tree_arrays,
            histograms,
            metadata,
            np,
            uproot,
        )
        validate_root_file(
            temporary_path,
            expected_tree_entries=len(tree_arrays["sensor_index"]),
            expected_histogram_names=tuple(histograms),
        )
        if overwrite:
            os.replace(temporary_path, target)
        else:
            try:
                os.link(temporary_path, target)
            except FileExistsError as exc:
                raise RootTemperatureWriterError(
                    f"ROOT output already exists: {target}; use --overwrite to replace it"
                ) from exc
            temporary_path.unlink()
    except RootTemperatureWriterError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise RootTemperatureWriterError(f"Cannot write ROOT output {target}: {exc}") from exc
    finally:
        temporary_path.unlink(missing_ok=True)

    bin_count = len(next(iter(histograms.values())).contents)
    short_names = _validate_sensor_configuration(pvs)
    sensors_with_data = tuple(
        short_name
        for pv, short_name in zip(pvs, short_names)
        if samples_by_pv[pv]
    )
    empty_sensors = tuple(
        short_name
        for pv, short_name in zip(pvs, short_names)
        if not samples_by_pv[pv]
    )
    return RootWriteResult(
        path=target,
        tree_entries=len(tree_arrays["sensor_index"]),
        histogram_bins=bin_count,
        bin_width_seconds=bin_width_seconds,
        sensors_with_data=sensors_with_data,
        empty_sensors=empty_sensors,
        file_size_bytes=target.stat().st_size,
    )
