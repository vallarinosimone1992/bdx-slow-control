from dataclasses import replace
from datetime import timedelta, timezone
import io
import json
from pathlib import Path

import pytest

from bdx_slow_control.config import ConfigurationError
from bdx_slow_control.run_temperature_export import (
    DEFAULT_HISTOGRAM_BIN_WIDTH_SECONDS,
    RUN_ROOT_ENV,
    RunTemperatureExportError,
    format_run_pattern,
    load_run_temperature_export_config,
    main,
    parse_caen_run_info,
    require_run_directory,
    resolve_info_file,
    resolve_output_file,
    resolve_run_directory,
    resolve_run_paths,
)


FIXTURE = Path(__file__).parent / "fixtures" / "caen" / "260714_info.txt"
EXPECTED_PVS = [
    "BDX:ENV:TEMP:T00:VALUE",
    "BDX:ENV:TEMP:T01:VALUE",
    "BDX:ENV:TEMP:T02:VALUE",
    "BDX:ENV:TEMP:T03:VALUE",
]


def _raw_config(run_root: Path | str) -> dict:
    return {
        "run_root": str(run_root),
        "run_directory_pattern": "{run_id}",
        "info_filename_pattern": "{run_id}_info.txt",
        "output_filename_pattern": "OTHER/SlowControl_{run_id}.root",
        "run_time_zone": "Europe/Rome",
        "histogram_bin_width_seconds": 5.0,
        "archiver": {
            "retrieval_url": "http://127.0.0.1:17668/retrieval",
            "timeout_seconds": 10,
        },
        "pvs": EXPECTED_PVS,
    }


def _write_config(tmp_path: Path, run_root: Path | str, **updates) -> Path:
    raw = _raw_config(run_root)
    raw.update(updates)
    path = tmp_path / "run_temperature_export.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _write_info(path: Path, text: str | None = None) -> None:
    path.write_text(text if text is not None else FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")


def _prepare_run(tmp_path: Path) -> tuple[Path, Path, Path]:
    run_root = tmp_path / "DAQ"
    run_directory = run_root / "260714"
    run_directory.mkdir(parents=True)
    info_file = run_directory / "260714_info.txt"
    _write_info(info_file)
    return run_root, run_directory, info_file


def test_parse_caen_run_info_real_fixture():
    metadata = parse_caen_run_info(FIXTURE, "260714", "Europe/Rome")

    assert metadata.run_id == "260714"
    assert metadata.start_time.isoformat() == "2026-07-14T16:16:04+02:00"
    assert metadata.stop_time.isoformat() == "2026-07-15T11:04:15+02:00"


def test_caen_timestamps_are_timezone_aware():
    metadata = parse_caen_run_info(FIXTURE, "260714", "Europe/Rome")

    assert metadata.start_time.tzinfo is not None
    assert metadata.stop_time.tzinfo is not None
    assert metadata.start_time.utcoffset() == timedelta(hours=2)
    assert metadata.stop_time.utcoffset() == timedelta(hours=2)


def test_caen_timestamps_convert_to_expected_utc_values():
    metadata = parse_caen_run_info(FIXTURE, "260714", "Europe/Rome")

    assert metadata.start_time.astimezone(timezone.utc).isoformat() == "2026-07-14T14:16:04+00:00"
    assert metadata.stop_time.astimezone(timezone.utc).isoformat() == "2026-07-15T09:04:15+00:00"


def test_caen_run_duration_is_correct():
    metadata = parse_caen_run_info(FIXTURE, "260714", "Europe/Rome")

    assert metadata.duration == timedelta(hours=18, minutes=48, seconds=11)
    assert metadata.duration.total_seconds() == 67691


def test_run_id_mismatch_is_rejected():
    with pytest.raises(RunTemperatureExportError, match="Run ID mismatch"):
        parse_caen_run_info(FIXTURE, "different-id", "Europe/Rome")


def test_missing_run_id_is_rejected(tmp_path: Path):
    info_file = tmp_path / "missing_run_id_info.txt"
    _write_info(
        info_file,
        "Start time = mar lug 14 16:16:04 2026\nStop time = mer lug 15 11:04:15 2026\n",
    )

    with pytest.raises(RunTemperatureExportError, match="Run ID is missing"):
        parse_caen_run_info(info_file, "260714", "Europe/Rome")


def test_missing_start_time_is_rejected(tmp_path: Path):
    info_file = tmp_path / "missing_start_info.txt"
    _write_info(info_file, "Run ID = 260714\nStop time = mer lug 15 11:04:15 2026\n")

    with pytest.raises(RunTemperatureExportError, match="Start time is missing"):
        parse_caen_run_info(info_file, "260714", "Europe/Rome")


def test_missing_stop_time_is_explicitly_rejected(tmp_path: Path):
    info_file = tmp_path / "active_run_info.txt"
    _write_info(info_file, "Run ID = 260714\nStart time = mar lug 14 16:16:04 2026\n")

    with pytest.raises(RunTemperatureExportError, match="active runs are not supported"):
        parse_caen_run_info(info_file, "260714", "Europe/Rome")


def test_invalid_italian_month_is_rejected(tmp_path: Path):
    info_file = tmp_path / "invalid_month_info.txt"
    _write_info(
        info_file,
        "Run ID = 260714\n"
        "Start time = mar xyz 14 16:16:04 2026\n"
        "Stop time = mer lug 15 11:04:15 2026\n",
    )

    with pytest.raises(RunTemperatureExportError, match="Invalid Italian month abbreviation"):
        parse_caen_run_info(info_file, "260714", "Europe/Rome")


def test_invalid_timestamp_format_is_rejected(tmp_path: Path):
    info_file = tmp_path / "invalid_time_info.txt"
    _write_info(
        info_file,
        "Run ID = 260714\n"
        "Start time = 2026-07-14 16:16:04\n"
        "Stop time = mer lug 15 11:04:15 2026\n",
    )

    with pytest.raises(RunTemperatureExportError, match="Invalid start time format"):
        parse_caen_run_info(info_file, "260714", "Europe/Rome")


def test_duplicate_metadata_field_is_rejected(tmp_path: Path):
    info_file = tmp_path / "duplicate_info.txt"
    _write_info(
        info_file,
        FIXTURE.read_text(encoding="utf-8") + "Run ID = 260714\n",
    )

    with pytest.raises(RunTemperatureExportError, match="Duplicate Run ID"):
        parse_caen_run_info(info_file, "260714", "Europe/Rome")


def test_weekday_word_is_not_semantically_validated(tmp_path: Path):
    info_file = tmp_path / "arbitrary_weekday_info.txt"
    _write_info(
        info_file,
        "Run ID = 260714\n"
        "Start time = qualsiasi lug 14 16:16:04 2026\n"
        "Stop time = parola lug 15 11:04:15 2026\n",
    )

    metadata = parse_caen_run_info(info_file, "260714", "Europe/Rome")

    assert metadata.start_time.isoformat() == "2026-07-14T16:16:04+02:00"


@pytest.mark.parametrize(
    ("timestamp", "error"),
    [
        ("dom mar 29 02:30:00 2026", "Nonexistent local start time"),
        ("dom ott 25 02:30:00 2026", "Ambiguous local start time"),
    ],
)
def test_dst_transition_times_are_rejected(tmp_path: Path, timestamp: str, error: str):
    info_file = tmp_path / "dst_info.txt"
    _write_info(
        info_file,
        "Run ID = 260714\n"
        f"Start time = {timestamp}\n"
        "Stop time = lun ott 26 11:04:15 2026\n",
    )

    with pytest.raises(RunTemperatureExportError, match=error):
        parse_caen_run_info(info_file, "260714", "Europe/Rome")


def test_invalid_timezone_is_rejected():
    with pytest.raises(RunTemperatureExportError, match="Invalid timezone"):
        parse_caen_run_info(FIXTURE, "260714", "Invalid/Timezone")


def test_stop_before_start_is_rejected(tmp_path: Path):
    info_file = tmp_path / "reversed_info.txt"
    _write_info(
        info_file,
        "Run ID = 260714\n"
        "Start time = mer lug 15 11:04:15 2026\n"
        "Stop time = mar lug 14 16:16:04 2026\n",
    )

    with pytest.raises(RunTemperatureExportError, match="is before start time"):
        parse_caen_run_info(info_file, "260714", "Europe/Rome")


def test_missing_metadata_file_is_rejected(tmp_path: Path):
    with pytest.raises(RunTemperatureExportError, match="metadata file does not exist"):
        parse_caen_run_info(tmp_path / "260714_info.txt", "260714", "Europe/Rome")


def test_missing_run_directory_is_rejected(tmp_path: Path):
    with pytest.raises(RunTemperatureExportError, match="Run directory does not exist"):
        require_run_directory(tmp_path / "missing-run")


def test_paths_are_resolved_from_configured_patterns(tmp_path: Path):
    config_path = _write_config(tmp_path, tmp_path / "DAQ")
    config = load_run_temperature_export_config(config_path, environ={})

    paths = resolve_run_paths(config, "260714")

    assert paths.run_directory == tmp_path / "DAQ" / "260714"
    assert paths.info_file == tmp_path / "DAQ" / "260714" / "260714_info.txt"
    assert paths.output_file == (
        tmp_path / "DAQ" / "260714" / "OTHER" / "SlowControl_260714.root"
    )


def test_absolute_run_root_is_allowed_and_normalized(tmp_path: Path):
    configured_root = tmp_path / "intermediate" / ".." / "DAQ"
    config_path = _write_config(tmp_path, configured_root)
    config = load_run_temperature_export_config(config_path, environ={})

    assert resolve_run_directory(config, "opaque-run") == tmp_path / "DAQ" / "opaque-run"


def test_symlink_escape_from_run_root_is_rejected(tmp_path: Path):
    run_root = tmp_path / "DAQ"
    outside = tmp_path / "outside"
    run_root.mkdir()
    outside.mkdir()
    (run_root / "260714").symlink_to(outside, target_is_directory=True)
    config_path = _write_config(tmp_path, run_root)
    config = load_run_temperature_export_config(config_path, environ={})

    with pytest.raises(ConfigurationError, match="resolves outside"):
        resolve_run_directory(config, "260714")


@pytest.mark.parametrize(
    ("field", "value", "resolver"),
    [
        ("run_directory_pattern", "../{run_id}", resolve_run_directory),
        ("info_filename_pattern", "../../outside_info.txt", resolve_info_file),
        ("output_filename_pattern", "../../outside.root", resolve_output_file),
    ],
)
def test_paths_that_escape_their_allowed_directory_are_rejected(
    tmp_path: Path,
    field: str,
    value: str,
    resolver,
):
    config_path = _write_config(tmp_path, tmp_path / "DAQ")
    config = load_run_temperature_export_config(config_path, environ={})
    config = replace(config, **{field: value})

    with pytest.raises(ConfigurationError, match="resolves outside"):
        resolver(config, "260714")


def test_unknown_pattern_field_is_rejected():
    with pytest.raises(ConfigurationError, match="Unknown field"):
        format_run_pattern("{date}/{run_id}", "260714", setting_name="test_pattern")


@pytest.mark.parametrize("run_id", ["../260714", "260714/part", "260714\\part", "run..id"])
def test_run_id_cannot_contain_path_traversal(run_id: str, tmp_path: Path):
    config_path = _write_config(tmp_path, tmp_path / "DAQ")
    config = load_run_temperature_export_config(config_path, environ={})

    with pytest.raises(RunTemperatureExportError, match="must not contain"):
        resolve_run_directory(config, run_id)


def test_run_root_is_loaded_from_json_without_requiring_it_to_exist(tmp_path: Path):
    configured_root = tmp_path / "not-created"
    config_path = _write_config(tmp_path, configured_root)

    config = load_run_temperature_export_config(config_path, environ={})

    assert config.run_root == configured_root
    assert config.run_root_source == "JSON"
    assert not configured_root.exists()


def test_environment_overrides_json_run_root(tmp_path: Path):
    config_path = _write_config(tmp_path, tmp_path / "json-root")
    environment_root = tmp_path / "environment-root"

    config = load_run_temperature_export_config(
        config_path,
        environ={RUN_ROOT_ENV: str(environment_root)},
    )

    assert config.run_root == environment_root
    assert config.run_root_source == "environment"


def test_empty_environment_run_root_is_ignored(tmp_path: Path):
    json_root = tmp_path / "json-root"
    config_path = _write_config(tmp_path, json_root)

    config = load_run_temperature_export_config(
        config_path,
        environ={RUN_ROOT_ENV: "  "},
    )

    assert config.run_root == json_root
    assert config.run_root_source == "JSON"


def test_cli_run_root_overrides_environment(tmp_path: Path):
    config_path = _write_config(tmp_path, tmp_path / "json-root")
    cli_root = tmp_path / "cli-root"

    config = load_run_temperature_export_config(
        config_path,
        cli_run_root=cli_root,
        environ={RUN_ROOT_ENV: str(tmp_path / "environment-root")},
    )

    assert config.run_root == cli_root
    assert config.run_root_source == "CLI"


def test_empty_final_run_root_is_rejected(tmp_path: Path):
    config_path = _write_config(tmp_path, "")

    with pytest.raises(ConfigurationError, match="run_root is missing or empty"):
        load_run_temperature_export_config(config_path, environ={RUN_ROOT_ENV: "  "})


def test_invalid_configured_timezone_is_rejected(tmp_path: Path):
    config_path = _write_config(
        tmp_path,
        tmp_path / "DAQ",
        run_time_zone="Invalid/Timezone",
    )

    with pytest.raises(ConfigurationError, match="Invalid run_time_zone"):
        load_run_temperature_export_config(config_path, environ={})


def test_missing_histogram_bin_width_uses_five_second_default(tmp_path: Path):
    raw = _raw_config(tmp_path / "DAQ")
    del raw["histogram_bin_width_seconds"]
    config_path = tmp_path / "run_temperature_export.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    config = load_run_temperature_export_config(config_path, environ={})

    assert config.histogram_bin_width_seconds == DEFAULT_HISTOGRAM_BIN_WIDTH_SECONDS == 5.0


@pytest.mark.parametrize(
    "invalid_value",
    [0, 0.0, -1, -0.5, True, False, "5", "invalid", None, [], {}],
)
def test_invalid_histogram_bin_width_is_rejected(tmp_path: Path, invalid_value):
    config_path = _write_config(
        tmp_path,
        tmp_path / "DAQ",
        histogram_bin_width_seconds=invalid_value,
    )

    with pytest.raises(ConfigurationError, match="finite number greater than zero"):
        load_run_temperature_export_config(config_path, environ={})


def test_cli_dry_run_prints_complete_summary(tmp_path: Path):
    run_root, run_directory, info_file = _prepare_run(tmp_path)
    config_path = _write_config(tmp_path, run_root)
    output = io.StringIO()
    error_output = io.StringIO()

    exit_code = main(
        ["260714", "--config", str(config_path), "--dry-run"],
        output=output,
        error_output=error_output,
        environ={},
    )

    summary = output.getvalue()
    assert exit_code == 0
    assert error_output.getvalue() == ""
    assert "Run ID: 260714" in summary
    assert "run_root source: JSON" in summary
    assert f"Run directory: {run_directory}" in summary
    assert f"Metadata file: {info_file}" in summary
    assert "Start local: 2026-07-14T16:16:04+02:00" in summary
    assert "Stop local: 2026-07-15T11:04:15+02:00" in summary
    assert "Start UTC: 2026-07-14T14:16:04+00:00" in summary
    assert "Stop UTC: 2026-07-15T09:04:15+00:00" in summary
    assert "Duration: 18:48:11 (67691 seconds)" in summary
    assert f"Future ROOT output: {run_directory / 'OTHER' / 'SlowControl_260714.root'}" in summary
    assert "Histogram bin width: 5 seconds" in summary
    assert all(pv in summary for pv in EXPECTED_PVS)


def test_cli_dry_run_creates_no_files_or_directories(tmp_path: Path):
    run_root, run_directory, _ = _prepare_run(tmp_path)
    config_path = _write_config(tmp_path, run_root)
    before = {path.relative_to(run_directory) for path in run_directory.rglob("*")}

    exit_code = main(
        ["260714", "--config", str(config_path), "--dry-run"],
        output=io.StringIO(),
        error_output=io.StringIO(),
        environ={},
    )

    after = {path.relative_to(run_directory) for path in run_directory.rglob("*")}
    assert exit_code == 0
    assert after == before
    assert not (run_directory / "OTHER").exists()


def test_cli_requires_dry_run(tmp_path: Path):
    config_path = _write_config(tmp_path, tmp_path / "DAQ")
    error_output = io.StringIO()

    exit_code = main(
        ["260714", "--config", str(config_path)],
        output=io.StringIO(),
        error_output=error_output,
        environ={},
    )

    assert exit_code == 2
    assert "--dry-run is required" in error_output.getvalue()


def test_cli_help_uses_only_underscore_command_name(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert help_text.startswith("usage: bdx_run_temperature_export")
    assert "bdx-run-temperature-export" not in help_text


def test_project_registers_only_underscore_command_name():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    expected = 'bdx_run_temperature_export = "bdx_slow_control.run_temperature_export:main"'
    assert expected in pyproject
    assert "bdx-run-temperature-export" not in pyproject


def test_cli_input_error_is_concise_and_nonzero(tmp_path: Path):
    config_path = _write_config(tmp_path, tmp_path / "DAQ")
    error_output = io.StringIO()

    exit_code = main(
        ["../invalid", "--config", str(config_path), "--dry-run"],
        output=io.StringIO(),
        error_output=error_output,
        environ={},
    )

    assert exit_code != 0
    assert error_output.getvalue().startswith("Error: Run ID must not contain")
    assert "Traceback" not in error_output.getvalue()
