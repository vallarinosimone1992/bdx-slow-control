from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest
import uproot

from bdx_slow_control import __version__
from bdx_slow_control.archiver_retrieval import ArchivedSample, datetime_to_timestamp_ns
import bdx_slow_control.root_temperature_writer as writer
from bdx_slow_control.root_temperature_writer import (
    METADATA_NAME,
    TREE_NAME,
    RootExportDependencyError,
    RootTemperatureWriterError,
    build_histograms,
    build_metadata,
    prepare_tree_arrays,
    validate_root_file,
    write_root_file,
)


PVS = (
    "BDX:ENV:TEMP:T00:VALUE",
    "BDX:ENV:TEMP:T01:VALUE",
    "BDX:ENV:TEMP:T02:VALUE",
    "BDX:ENV:TEMP:T03:VALUE",
)
START_LOCAL = datetime(2026, 7, 14, 16, 16, 4, tzinfo=ZoneInfo("Europe/Rome"))
STOP_LOCAL = START_LOCAL + timedelta(seconds=12)


def _open_root(path: Path):
    return uproot.open(path, handler=uproot.source.file.MultithreadedFileSource)


def _sample(
    pv: str,
    offset_seconds: int,
    nanoseconds: int,
    value: float,
    status=0,
    severity=0,
) -> ArchivedSample:
    start_seconds = int(START_LOCAL.astimezone(timezone.utc).timestamp())
    seconds = start_seconds + offset_seconds
    timestamp_ns = seconds * 1_000_000_000 + nanoseconds
    stamp = datetime.fromtimestamp(seconds, timezone.utc)
    return ArchivedSample(
        pv=pv,
        seconds=seconds,
        nanoseconds=nanoseconds,
        timestamp_ns=timestamp_ns,
        timestamp_utc=f"{stamp:%Y-%m-%dT%H:%M:%S}.{nanoseconds:09d}Z",
        value=value,
        status=status,
        severity=severity,
    )


def _samples() -> dict[str, list[ArchivedSample]]:
    return {
        PVS[0]: [
            _sample(PVS[0], 7, 250_000_000, 20.0),
            _sample(PVS[0], 0, 100_000_000, 10.0),
            _sample(PVS[0], 1, 100_000_000, 14.0, None, None),
        ],
        PVS[1]: [
            _sample(PVS[1], 0, 100_000_000, 30.0, "NO_ALARM", "NO_ALARM"),
            _sample(PVS[1], 12, 0, 32.0),
        ],
        PVS[2]: [],
        PVS[3]: [_sample(PVS[3], 0, 100_000_000, 40.0)],
    }


def _write(path: Path, *, overwrite: bool = False):
    return write_root_file(
        path,
        run_id="260714",
        start_local=START_LOCAL,
        stop_local=STOP_LOCAL,
        caen_time_zone="Europe/Rome",
        bin_width_seconds=5.0,
        pvs=PVS,
        samples_by_pv=_samples(),
        archiver_endpoint="http://127.0.0.1:17668/retrieval",
        overwrite=overwrite,
    )


def _candidate_parts():
    tree_arrays = prepare_tree_arrays(PVS, _samples(), START_LOCAL, STOP_LOCAL)
    histograms = build_histograms(PVS, _samples(), START_LOCAL, STOP_LOCAL, 5.0)
    metadata = build_metadata(
        run_id="260714",
        start_local=START_LOCAL,
        stop_local=STOP_LOCAL,
        caen_time_zone="Europe/Rome",
        bin_width_seconds=5.0,
        pvs=PVS,
        samples_by_pv=_samples(),
        archiver_endpoint="http://127.0.0.1:17668/retrieval",
    )
    return tree_arrays, histograms, metadata


def test_prepare_tree_arrays_is_lossless_sorted_and_typed():
    arrays = prepare_tree_arrays(PVS, _samples(), START_LOCAL, STOP_LOCAL)

    assert list(arrays) == list(writer.TREE_SCHEMA)
    assert {name: array.dtype for name, array in arrays.items()} == {
        "sensor_id": np.dtype("int32"),
        "timestamp_ns": np.dtype("int64"),
        "time_from_run_start_s": np.dtype("float64"),
        "temperature": np.dtype("float64"),
        "status": np.dtype("int32"),
        "severity": np.dtype("int32"),
    }
    assert arrays["sensor_id"].tolist() == [0, 1, 3, 0, 0, 1]
    assert arrays["temperature"].tolist() == [10.0, 30.0, 40.0, 14.0, 20.0, 32.0]
    assert arrays["time_from_run_start_s"].tolist() == pytest.approx(
        [0.1, 0.1, 0.1, 1.1, 7.25, 12.0]
    )
    assert arrays["status"].tolist() == [0, 0, 0, -1, 0, 0]
    assert arrays["severity"].tolist() == [0, 0, 0, -1, 0, 0]
    assert 2 not in arrays["sensor_id"]
    assert not ({"sensor_index", "seconds", "nanoseconds", "value"} & arrays.keys())

    timestamps = arrays["timestamp_ns"].tolist()
    assert timestamps == sorted(timestamps)
    assert [
        (timestamp_ns // 1_000_000_000, timestamp_ns % 1_000_000_000)
        for timestamp_ns in timestamps
    ] == [
        (sample.seconds, sample.nanoseconds)
        for sample in sorted(
            (sample for samples in _samples().values() for sample in samples),
            key=lambda sample: (
                sample.timestamp_ns,
                PVS.index(sample.pv),
            ),
        )
    ]


def test_relative_time_preserves_epics_nanoseconds_without_rounding():
    samples = {pv: [] for pv in PVS}
    offsets = (
        (0, 0, 0.0),
        (0, 100_000_000, 0.1),
        (0, 999_999_999, 0.999999999),
        (1, 0, 1.0),
        (1, 100_000_000, 1.1),
    )
    samples[PVS[0]] = [
        _sample(PVS[0], seconds, nanoseconds, float(index))
        for index, (seconds, nanoseconds, _expected) in enumerate(offsets)
    ]

    arrays = prepare_tree_arrays(PVS, samples, START_LOCAL, STOP_LOCAL)

    assert arrays["time_from_run_start_s"].tolist() == pytest.approx(
        [expected for _seconds, _nanoseconds, expected in offsets],
        rel=0,
        abs=1e-15,
    )


def test_sample_before_run_start_is_rejected():
    samples = {pv: [] for pv in PVS}
    samples[PVS[0]] = [_sample(PVS[0], -1, 999_999_999, 1.0)]

    with pytest.raises(RootTemperatureWriterError, match="precedes the run start"):
        prepare_tree_arrays(PVS, samples, START_LOCAL, STOP_LOCAL)


def test_sample_after_run_stop_is_rejected():
    samples = {pv: [] for pv in PVS}
    samples[PVS[0]] = [_sample(PVS[0], 12, 1, 1.0)]

    with pytest.raises(RootTemperatureWriterError, match="after the run stop"):
        prepare_tree_arrays(PVS, samples, START_LOCAL, STOP_LOCAL)


def test_tree_and_histograms_use_the_same_run_start_origin():
    samples = {pv: [] for pv in PVS}
    samples[PVS[0]] = [
        _sample(PVS[0], 0, 0, 10.0),
        _sample(PVS[0], 0, 999_999_999, 12.0),
        _sample(PVS[0], 1, 0, 20.0),
        _sample(PVS[0], 1, 100_000_000, 22.0),
    ]

    arrays = prepare_tree_arrays(PVS, samples, START_LOCAL, STOP_LOCAL)
    histograms = build_histograms(PVS, samples, START_LOCAL, STOP_LOCAL, 1.0)

    assert arrays["time_from_run_start_s"].tolist() == pytest.approx(
        [0.0, 0.999999999, 1.0, 1.1], rel=0, abs=1e-15
    )
    assert histograms["temperature_T00_counts"].contents[:2].tolist() == [2.0, 2.0]


def test_build_histograms_has_required_binning_means_sem_and_counts():
    histograms = build_histograms(
        PVS,
        _samples(),
        START_LOCAL,
        STOP_LOCAL,
        5.0,
    )

    assert len(histograms) == 8
    temperature = histograms["temperature_T00"]
    counts = histograms["temperature_T00_counts"]
    assert temperature.bin_edges.tolist() == [0.0, 5.0, 10.0, 15.0]
    assert temperature.bin_edges[0] == 0
    assert temperature.bin_edges[-1] >= 12
    assert temperature.contents.tolist() == [12.0, 20.0, 0.0]
    assert temperature.errors.tolist() == pytest.approx([2.0, 0.0, 0.0])
    assert counts.contents.tolist() == [2.0, 1.0, 0.0]
    assert counts.errors.tolist() == [0.0, 0.0, 0.0]
    assert histograms["temperature_T01_counts"].contents.tolist() == [1.0, 0.0, 1.0]
    assert histograms["temperature_T02"].contents.tolist() == [0.0, 0.0, 0.0]
    assert histograms["temperature_T02"].errors.tolist() == [0.0, 0.0, 0.0]
    assert histograms["temperature_T02_counts"].contents.tolist() == [0.0, 0.0, 0.0]


def test_build_metadata_contains_provenance_mapping_and_empty_sensor():
    generated = datetime(2026, 7, 16, 10, 20, 30, tzinfo=timezone.utc)
    metadata = build_metadata(
        run_id="260714",
        start_local=START_LOCAL,
        stop_local=STOP_LOCAL,
        caen_time_zone="Europe/Rome",
        bin_width_seconds=5.0,
        pvs=PVS,
        samples_by_pv=_samples(),
        archiver_endpoint="http://archiver/retrieval",
        generated_at_utc=generated,
        package_version="test-version",
        git_commit="abc123",
    )

    assert metadata["format"] == "bdx-run-temperature-export-metadata-v2"
    assert metadata["tree_name"] == "temperature_samples"
    assert metadata["tree_schema"] == writer.TREE_SCHEMA
    assert metadata["tree_time_semantics"] == {
        "relative_branch": "time_from_run_start_s",
        "relative_unit": "second",
        "relative_type": "float64",
        "relative_origin": "CAEN run start from <run_id>_info.txt",
        "relative_calculation": "(timestamp_ns - run_start_timestamp_ns) / 1e9",
        "absolute_branch": "timestamp_ns",
        "absolute_unit": "nanosecond since Unix epoch",
    }
    assert metadata["run_id"] == "260714"
    assert metadata["start_local"] == "2026-07-14T16:16:04+02:00"
    assert metadata["stop_local"] == "2026-07-14T16:16:16+02:00"
    assert metadata["start_utc"] == "2026-07-14T14:16:04+00:00"
    assert metadata["stop_utc"] == "2026-07-14T14:16:16+00:00"
    assert metadata["duration_seconds"] == 12.0
    assert metadata["caen_time_zone"] == "Europe/Rome"
    assert metadata["histogram_bin_width_seconds"] == 5.0
    assert metadata["configured_pv_count"] == 4
    assert "total_sample_count" in metadata
    assert "total_samples" not in metadata
    assert type(metadata["total_sample_count"]) is int
    assert metadata["total_sample_count"] == 6
    assert metadata["missing_status_severity_sentinel"] == -1
    assert "canonical integer codes" in metadata["alarm_field_encoding"]
    assert metadata["sensor_mapping"] == [
        {"sensor_id": sensor_id, "short_name": f"T{sensor_id:02d}", "pv": pv}
        for sensor_id, pv in enumerate(PVS)
    ]
    assert metadata["sensors"][2] == {
        "sensor_id": 2,
        "short_name": "T02",
        "pv": PVS[2],
        "sample_count": 0,
    }
    assert metadata["archiver_endpoint"] == "http://archiver/retrieval"
    assert metadata["generated_at_utc"] == generated.isoformat()
    assert metadata["package_version"] == "test-version"
    assert metadata["git_commit"] == "abc123"
    assert "TObjString" in metadata["storage"]


def test_written_file_contains_real_ttree_th1d_errors_and_json_metadata(tmp_path: Path):
    other = tmp_path / "260714" / "OTHER"
    other.mkdir(parents=True)
    target = other / "SlowControl_260714.root"

    result = _write(target)

    assert result.path == target
    assert result.tree_entries == 6
    assert result.histogram_bins == 3
    assert result.bin_width_seconds == 5.0
    assert result.sensors_with_data == ("T00", "T01", "T03")
    assert result.empty_sensors == ("T02",)
    assert result.file_size_bytes == target.stat().st_size > 0
    assert not list(other.glob(f".{target.name}.*.tmp"))

    with _open_root(target) as root_file:
        assert root_file[TREE_NAME].classname == "TTree"
        assert "RNTuple" not in root_file[TREE_NAME].classname
        assert root_file[TREE_NAME].num_entries == 6
        assert root_file[TREE_NAME].typenames() == {
            "sensor_id": "int32_t",
            "timestamp_ns": "int64_t",
            "time_from_run_start_s": "double",
            "temperature": "double",
            "status": "int32_t",
            "severity": "int32_t",
        }
        arrays = root_file[TREE_NAME].arrays(library="np")
        assert arrays["sensor_id"].tolist() == [0, 1, 3, 0, 0, 1]
        assert 2 not in arrays["sensor_id"]
        assert not ({"sensor_index", "seconds", "nanoseconds", "value"} & arrays.keys())

        for short_name in ("T00", "T01", "T02", "T03"):
            for suffix in ("", "_counts"):
                histogram = root_file[f"temperature_{short_name}{suffix}"]
                assert histogram.classname == "TH1D"
                assert histogram.axis().edges()[0] == 0
                assert histogram.axis().edges()[-1] >= 12
        temperature = root_file["temperature_T00"]
        counts = root_file["temperature_T00_counts"]
        assert temperature.values().tolist() == [12.0, 20.0, 0.0]
        assert temperature.errors().tolist() == pytest.approx([2.0, 0.0, 0.0])
        assert counts.values().tolist() == [2.0, 1.0, 0.0]
        assert counts.errors().tolist() == [0.0, 0.0, 0.0]
        assert root_file["temperature_T02"].values().tolist() == [0.0, 0.0, 0.0]
        assert root_file["temperature_T02_counts"].values().tolist() == [0.0, 0.0, 0.0]

        assert root_file[METADATA_NAME].classname == "TObjString"
        metadata = json.loads(str(root_file[METADATA_NAME]))
        assert metadata["format"] == "bdx-run-temperature-export-metadata-v2"
        assert metadata["run_id"] == "260714"
        assert "total_sample_count" in metadata
        assert "total_samples" not in metadata
        assert type(metadata["total_sample_count"]) is int
        assert metadata["total_sample_count"] == root_file[TREE_NAME].num_entries
        assert metadata["total_sample_count"] == sum(
            sensor["sample_count"] for sensor in metadata["sensors"]
        )
        assert metadata["total_sample_count"] == sum(
            np.sum(root_file[f"temperature_{sensor['short_name']}_counts"].values())
            for sensor in metadata["sensors"]
        )
        assert metadata["package_version"] == __version__
        assert metadata["sensors"][2] == {
            "sensor_id": 2,
            "short_name": "T02",
            "pv": PVS[2],
            "sample_count": 0,
        }
        assert "git_commit" in metadata


def test_empty_export_still_writes_zero_entry_ttree_and_all_histograms(tmp_path: Path):
    other = tmp_path / "OTHER"
    other.mkdir()
    samples = {pv: [] for pv in PVS}
    target = other / "SlowControl_empty.root"

    write_root_file(
        target,
        run_id="empty",
        start_local=START_LOCAL,
        stop_local=STOP_LOCAL,
        caen_time_zone="Europe/Rome",
        bin_width_seconds=5.0,
        pvs=PVS,
        samples_by_pv=samples,
        archiver_endpoint="http://archiver/retrieval",
    )

    with _open_root(target) as root_file:
        assert root_file[TREE_NAME].classname == "TTree"
        assert root_file[TREE_NAME].num_entries == 0
        for short_name in ("T00", "T01", "T02", "T03"):
            assert np.all(root_file[f"temperature_{short_name}"].values() == 0)
            assert np.all(root_file[f"temperature_{short_name}_counts"].values() == 0)


def test_existing_file_is_refused_and_overwrite_is_explicit(tmp_path: Path):
    other = tmp_path / "OTHER"
    other.mkdir()
    target = other / "SlowControl_260714.root"
    target.write_bytes(b"original")

    with pytest.raises(RootTemperatureWriterError, match="use --overwrite"):
        _write(target)
    assert target.read_bytes() == b"original"

    result = _write(target, overwrite=True)

    assert result.path == target
    with _open_root(target) as root_file:
        assert root_file[TREE_NAME].num_entries == 6


def test_missing_other_directory_is_not_created(tmp_path: Path):
    other = tmp_path / "260714" / "OTHER"

    with pytest.raises(RootTemperatureWriterError, match="will not be created"):
        _write(other / "SlowControl_260714.root")

    assert not other.exists()


def test_temporary_file_is_removed_when_validation_fails(tmp_path: Path, monkeypatch):
    other = tmp_path / "OTHER"
    other.mkdir()
    target = other / "SlowControl_260714.root"

    def fail_validation(*_args, **_kwargs):
        raise RootTemperatureWriterError("simulated validation failure")

    monkeypatch.setattr(writer, "validate_root_file", fail_validation)

    with pytest.raises(RootTemperatureWriterError, match="simulated validation failure"):
        _write(target)

    assert not target.exists()
    assert not list(other.glob(f".{target.name}.*.tmp"))


@pytest.mark.parametrize(
    ("incompatibility", "error"),
    [
        ("metadata_type", "total_sample_count must be an integer"),
        ("metadata_tree_count", "does not match temperature_samples entry count"),
        ("sensor_count", "does not match TTree sensor_id count"),
        ("histogram_count", "do not match TTree sensor_id count"),
    ],
)
def test_incompatible_sample_counts_fail_validation_and_remove_temporary_file(
    tmp_path: Path,
    monkeypatch,
    incompatibility: str,
    error: str,
):
    other = tmp_path / "OTHER"
    other.mkdir()
    target = other / "SlowControl_260714.root"
    write_temporary_file = writer._write_temporary_file

    def write_incompatible_file(
        path,
        tree_arrays,
        histograms,
        metadata,
        np_module,
        uproot_module,
    ):
        write_temporary_file(
            path,
            tree_arrays,
            histograms,
            metadata,
            np_module,
            uproot_module,
        )
        with uproot_module.update(path) as root_file:
            if incompatibility != "histogram_count":
                incompatible_metadata = json.loads(json.dumps(metadata))
                if incompatibility == "metadata_type":
                    incompatible_metadata["total_sample_count"] = float(
                        incompatible_metadata["total_sample_count"]
                    )
                elif incompatibility == "metadata_tree_count":
                    incompatible_metadata["total_sample_count"] += 1
                else:
                    incompatible_metadata["sensors"][0]["sample_count"] += 1
                root_file[METADATA_NAME] = json.dumps(incompatible_metadata)
            else:
                counts = histograms["temperature_T00_counts"]
                incompatible_contents = counts.contents.copy()
                incompatible_contents[0] += 1
                root_file[counts.name] = writer._to_root_histogram(
                    replace(counts, contents=incompatible_contents),
                    np_module,
                    uproot_module,
                )

    monkeypatch.setattr(writer, "_write_temporary_file", write_incompatible_file)

    with pytest.raises(RootTemperatureWriterError, match=error):
        _write(target)

    assert not target.exists()
    assert not list(other.glob(f".{target.name}.*.tmp"))


def test_validation_rejects_missing_expected_histogram(tmp_path: Path):
    target = tmp_path / "incomplete.root"
    with uproot.recreate(target) as root_file:
        root_file.mktree(TREE_NAME, writer.TREE_SCHEMA)
        root_file[METADATA_NAME] = json.dumps(
            {
                "format": writer.METADATA_FORMAT,
                "total_sample_count": 0,
                "sensors": [],
            }
        )

    with pytest.raises(RootTemperatureWriterError, match="missing histogram"):
        validate_root_file(
            target,
            expected_tree_entries=0,
            expected_histogram_names=("temperature_T00",),
        )


@pytest.mark.parametrize(
    ("schema_update", "error"),
    [
        ({"sensor_id": "int64"}, "branch types"),
        ({"seconds": "int64"}, "contains removed branches: seconds"),
    ],
)
def test_validation_rejects_wrong_types_and_removed_branches(
    tmp_path: Path,
    schema_update: dict[str, str],
    error: str,
):
    target = tmp_path / "wrong-schema.root"
    schema = dict(writer.TREE_SCHEMA)
    schema.update(schema_update)
    with uproot.recreate(target) as root_file:
        root_file.mktree(TREE_NAME, schema)

    with pytest.raises(RootTemperatureWriterError, match=error):
        validate_root_file(
            target,
            expected_tree_entries=0,
            expected_histogram_names=(),
        )


@pytest.mark.parametrize(
    ("corruption", "error"),
    [
        ("negative_time", "contains negative values"),
        ("inconsistent_time", "is inconsistent with timestamp_ns"),
        ("ordering", "not sorted by timestamp_ns and then sensor_id"),
        ("mapping", "does not match configured PV order"),
    ],
)
def test_validation_rejects_tree_time_ordering_and_mapping_corruption(
    tmp_path: Path,
    corruption: str,
    error: str,
):
    target = tmp_path / f"{corruption}.root"
    tree_arrays, histograms, metadata = _candidate_parts()
    tree_arrays = {name: values.copy() for name, values in tree_arrays.items()}
    metadata = json.loads(json.dumps(metadata))
    if corruption == "negative_time":
        tree_arrays["time_from_run_start_s"][0] = -1.0
    elif corruption == "inconsistent_time":
        tree_arrays["time_from_run_start_s"][0] += 0.01
    elif corruption == "ordering":
        for values in tree_arrays.values():
            values[[0, 1]] = values[[1, 0]]
    else:
        metadata["sensor_mapping"][0]["pv"] = "BDX:ENV:TEMP:WRONG:VALUE"
        metadata["sensors"][0]["pv"] = "BDX:ENV:TEMP:WRONG:VALUE"

    writer._write_temporary_file(
        target,
        tree_arrays,
        histograms,
        metadata,
        np,
        uproot,
    )

    with pytest.raises(RootTemperatureWriterError, match=error):
        validate_root_file(
            target,
            expected_tree_entries=len(tree_arrays["sensor_id"]),
            expected_histogram_names=tuple(histograms),
            expected_sensor_mapping=tuple(
                (sensor_id, f"T{sensor_id:02d}", pv)
                for sensor_id, pv in enumerate(PVS)
            ),
            expected_run_start_timestamp_ns=datetime_to_timestamp_ns(START_LOCAL),
            expected_run_stop_timestamp_ns=datetime_to_timestamp_ns(STOP_LOCAL),
        )


def test_optional_dependency_error_is_operationally_clear(monkeypatch):
    real_import = writer.importlib.import_module

    def import_without_export_dependencies(name):
        if name in {"numpy", "uproot"}:
            raise ModuleNotFoundError(name)
        return real_import(name)

    monkeypatch.setattr(writer.importlib, "import_module", import_without_export_dependencies)

    with pytest.raises(RootExportDependencyError) as exc_info:
        prepare_tree_arrays(PVS, _samples(), START_LOCAL, STOP_LOCAL)

    message = str(exc_info.value)
    assert "ROOT export requires" in message
    assert "NumPy and uproot" in message
    assert "export" in message


def test_time_from_start_uses_integer_nanoseconds():
    samples = {pv: [] for pv in PVS}
    samples[PVS[0]] = [_sample(PVS[0], 0, 123_456_789, 1.0)]

    arrays = prepare_tree_arrays(PVS, samples, START_LOCAL, STOP_LOCAL)

    assert datetime_to_timestamp_ns(START_LOCAL) % 1_000_000_000 == 0
    assert arrays["time_from_run_start_s"][0] == pytest.approx(0.123456789)
