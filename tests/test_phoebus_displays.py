from pathlib import Path
import os
import subprocess
import xml.etree.ElementTree as ET

from bdx_slow_control.phoebus_generator import archiver_pbraw_url, generate


PROTOTYPE_PROFILE = Path("config/profiles/prototype")
MAIN_SERVER_PROFILE = Path("config/profiles/main-server")
RASPBERRY_PROFILE = Path("config/profiles/raspberry")
RASPBERRY_TEMPERATURE_PVS = {
    "BDX:ENV:TEMP:T00:VALUE",
    "BDX:ENV:TEMP:T01:VALUE",
    "BDX:ENV:TEMP:T02:VALUE",
    "BDX:ENV:TEMP:T03:VALUE",
}
RASPBERRY_STATUS_OK_PVS = {
    "BDX:ENV:TEMP:T00:STATUS_OK",
    "BDX:ENV:TEMP:T01:STATUS_OK",
    "BDX:ENV:TEMP:T02:STATUS_OK",
    "BDX:ENV:TEMP:T03:STATUS_OK",
}
RASPBERRY_TEMPERATURE_LABELS = {"T00", "T01", "T02", "T03"}
ENVIRONMENT_SUMMARY_PVS = {
    "BDX:ENV:HEARTBEAT",
    "BDX:ENV:LAST_TEMPERATURE_UPDATE",
}


def _pv_references(path: Path) -> set[str]:
    root = ET.parse(path).getroot()
    references = set()
    for tag in ("pv_name", "y_pv"):
        for element in root.iter(tag):
            if element.text and element.text.startswith("BDX:"):
                references.add(element.text)
    return references


def _databrowsers(path: Path) -> list[ET.Element]:
    root = ET.parse(path).getroot()
    return [
        widget
        for widget in root.findall("widget")
        if widget.get("type") == "databrowser"
    ]


def _plt(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def _plt_traces(plt_root: ET.Element) -> set[str]:
    return {
        element.text
        for element in plt_root.findall("pvlist/pv/name")
        if element.text and element.text.startswith("BDX:")
    }


def _plt_trace_labels(plt_root: ET.Element) -> set[str]:
    return {
        element.text
        for element in plt_root.findall("pvlist/pv/display_name")
        if element.text
    }


def _plt_archive_urls(plt_root: ET.Element) -> set[str]:
    return {
        element.text
        for element in plt_root.findall("pvlist/pv/archive/url")
        if element.text
    }


def _text_updates(path: Path) -> list[ET.Element]:
    root = ET.parse(path).getroot()
    return [
        widget
        for widget in root.findall("widget")
        if widget.get("type") == "textupdate"
    ]


def _widgets(path: Path, widget_type: str) -> list[ET.Element]:
    root = ET.parse(path).getroot()
    return [
        widget
        for widget in root.findall("widget")
        if widget.get("type") == widget_type
    ]


def _labels(path: Path) -> set[str]:
    root = ET.parse(path).getroot()
    return {
        element.text
        for element in root.findall("widget/text")
        if element.text
    }


def _action_buttons(path: Path) -> list[ET.Element]:
    return _widgets(path, "action_button")


def _open_file_targets(path: Path) -> set[str]:
    root = ET.parse(path).getroot()
    return {
        element.text
        for element in root.findall(".//action[@type='open_file']/file")
        if element.text
    }


def _button_texts_for_pv(path: Path, pv_name: str) -> set[str]:
    return {
        widget.findtext("text")
        for widget in _action_buttons(path)
        if widget.findtext("pv_name") == pv_name
    }


def _confirmed_button_texts_for_pv(path: Path, pv_name: str) -> set[str]:
    return {
        widget.findtext("text")
        for widget in _action_buttons(path)
        if widget.findtext("pv_name") == pv_name
        and widget.findtext("confirm_dialog") == "true"
    }


def test_generated_displays_are_valid_xml_and_cover_every_pv(tmp_path: Path):
    pvs = generate(PROTOTYPE_PROFILE, tmp_path)
    for path in tmp_path.glob("*.bob"):
        assert ET.parse(path).getroot().tag == "display"
    for path in tmp_path.glob("*.plt"):
        assert ET.parse(path).getroot().tag == "databrowser"

    expected = {pv.name for pv in pvs}
    assert expected.issubset(_pv_references(tmp_path / "all_pvs.bob"))


def test_every_writable_pv_has_a_control_in_all_pvs(tmp_path: Path):
    pvs = generate(PROTOTYPE_PROFILE, tmp_path)
    root = ET.parse(tmp_path / "all_pvs.bob").getroot()
    controlled = set()
    for widget in root.findall("widget"):
        if widget.get("type") not in {"textentry", "bool_button", "action_button"}:
            continue
        pv_name = widget.findtext("pv_name")
        if pv_name:
            controlled.add(pv_name)

    writable = {pv.name for pv in pvs if not pv.read_only}
    assert writable.issubset(controlled)


def test_all_display_pv_references_and_navigation_targets_exist(tmp_path: Path):
    pvs = generate(PROTOTYPE_PROFILE, tmp_path)
    expected = {pv.name for pv in pvs}
    for path in tmp_path.glob("*.bob"):
        root = ET.parse(path).getroot()
        assert _pv_references(path).issubset(expected)
        for file_element in root.iter("file"):
            if file_element.text and file_element.text.endswith(".bob"):
                assert (tmp_path / file_element.text).exists()
            elif file_element.text and file_element.text.endswith(".plt"):
                assert (tmp_path / file_element.text).exists()


def test_generated_navigation_actions_open_new_tabs(tmp_path: Path):
    generate(PROTOTYPE_PROFILE, tmp_path)

    for path in tmp_path.glob("*.bob"):
        root = ET.parse(path).getroot()
        targets = [
            element.text
            for element in root.findall(".//action[@type='open_display']/target")
        ]
        assert targets
        assert set(targets) == {"tab"}


def test_generated_databrowser_plt_files_are_live_only_when_archiver_is_disabled(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.delenv("BDX_ARCHIVER_ENABLED", raising=False)
    monkeypatch.delenv("BDX_ARCHIVER_URL", raising=False)

    generate(PROTOTYPE_PROFILE, tmp_path)
    for path in tmp_path.glob("*.plt"):
        assert _plt(path).find("pvlist/pv/archive") is None
        assert {
            period.text
            for period in _plt(path).findall("pvlist/pv/period")
        } == {"5.0"}
        assert {
            request.text
            for request in _plt(path).findall("pvlist/pv/request")
        } == {"RAW"}


def test_generated_databrowser_plt_files_include_archive_source_when_enabled(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("BDX_ARCHIVER_ENABLED", "true")
    monkeypatch.setenv(
        "BDX_ARCHIVER_URL",
        "http://operator:secret@archiver.example:17668/retrieval",
    )
    monkeypatch.setenv("BDX_ARCHIVER_NAME", "BDX Prototype")

    generate(MAIN_SERVER_PROFILE, tmp_path, only="psu")

    for path in tmp_path.glob("psu_*.plt"):
        root = _plt(path)
        assert _plt_archive_urls(root) == {
            "pbraw://archiver.example:17668/retrieval"
        }
        assert {
            element.text
            for element in root.findall("pvlist/pv/archive/name")
        } == {"BDX Prototype"}
        assert "secret" not in path.read_text(encoding="utf-8")


def test_archiver_url_is_converted_to_valid_pbraw_endpoint():
    assert (
        archiver_pbraw_url("http://archiver.example:17668/retrieval")
        == "pbraw://archiver.example:17668/retrieval"
    )
    assert (
        archiver_pbraw_url("https://archiver.example:17668/retrieval/")
        == "pbraw://archiver.example:17668/retrieval"
    )
    assert (
        archiver_pbraw_url("operator:secret@archiver.example:17668/retrieval")
        == "pbraw://archiver.example:17668/retrieval"
    )
    assert archiver_pbraw_url("") is None


def test_enabled_archiver_with_empty_url_does_not_insert_invalid_archive_source(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("BDX_ARCHIVER_ENABLED", "true")
    monkeypatch.setenv("BDX_ARCHIVER_URL", "")
    monkeypatch.delenv("BDX_ARCHIVER_STRICT_CHECK", raising=False)

    generate(MAIN_SERVER_PROFILE, tmp_path, only="chiller")

    for path in tmp_path.glob("chiller_*.plt"):
        assert _plt(path).find("pvlist/pv/archive") is None


def test_raspberry_environment_display_groups_all_temperatures_in_one_databrowser_chart(
    tmp_path: Path,
):
    generate(RASPBERRY_PROFILE, tmp_path, only="environment")

    databrowsers = _databrowsers(tmp_path / "environment.bob")
    temperature_charts = [
        widget
        for widget in databrowsers
        if RASPBERRY_TEMPERATURE_PVS.issubset(
            _plt_traces(_plt(tmp_path / widget.findtext("file")))
        )
    ]

    assert len(temperature_charts) == 1
    plt_root = _plt(tmp_path / temperature_charts[0].findtext("file"))
    assert plt_root.findtext("title") == "Environment temperatures"
    assert plt_root.findtext("show_legend") == "true"
    assert plt_root.findtext("axes/axis/name") == "Temperature [degC]"
    assert plt_root.findtext("axes/axis/autoscale") == "true"
    assert _plt_traces(plt_root) == RASPBERRY_TEMPERATURE_PVS
    assert _plt_trace_labels(plt_root) == RASPBERRY_TEMPERATURE_LABELS


def test_raspberry_environment_display_uses_live_relative_time_window(
    tmp_path: Path,
):
    generate(RASPBERRY_PROFILE, tmp_path, only="environment")

    databrowsers = _databrowsers(tmp_path / "environment.bob")
    assert databrowsers
    for widget in databrowsers:
        plt_root = _plt(tmp_path / widget.findtext("file"))
        assert plt_root.findtext("start") == "-10 minutes"
        assert plt_root.findtext("end") == "now"
        assert plt_root.findtext("scroll") == "true"
        assert plt_root.findtext("update_period") == "1.0"
        assert widget.findtext("show_toolbar") == "true"


def test_raspberry_environment_display_contains_live_temperature_summary(
    tmp_path: Path,
):
    generate(RASPBERRY_PROFILE, tmp_path, only="environment")

    text_updates = _text_updates(tmp_path / "environment.bob")
    summary_values = {
        widget.findtext("pv_name")
        for widget in text_updates
        if widget.findtext("precision") == "2"
        and widget.findtext("format") == "1"
    }
    summary_pvs = {
        widget.findtext("pv_name")
        for widget in text_updates
    }
    status_leds = {
        widget.findtext("pv_name")
        for widget in _widgets(tmp_path / "environment.bob", "led")
    }

    assert summary_values == RASPBERRY_TEMPERATURE_PVS
    assert ENVIRONMENT_SUMMARY_PVS.issubset(summary_pvs)
    assert status_leds == RASPBERRY_STATUS_OK_PVS


def test_raspberry_environment_operator_display_links_to_expert_pv_display(
    tmp_path: Path,
):
    pvs = generate(RASPBERRY_PROFILE, tmp_path, only="environment")

    operator_display = tmp_path / "environment.bob"
    expert_display = tmp_path / "environment_expert.bob"
    assert ET.parse(operator_display).getroot().tag == "display"
    assert ET.parse(expert_display).getroot().tag == "display"

    operator_references = _pv_references(operator_display)
    expert_references = _pv_references(expert_display)
    assert "Expert PVs" in _labels(operator_display)
    assert "PV name" not in _labels(operator_display)
    assert "BDX:ENV:TEMP:T00:CLEAR_ERROR_CMD" not in operator_references
    assert "BDX:ENV:TEMP:T00:ERROR_MESSAGE" not in operator_references
    assert {pv.name for pv in pvs}.issubset(expert_references)


def test_raspberry_environment_display_contains_no_humidity_or_pressure_traces(
    tmp_path: Path,
):
    generate(RASPBERRY_PROFILE, tmp_path, only="environment")

    references = _pv_references(tmp_path / "environment.bob")
    assert not any(reference.startswith("BDX:ENV:HUMIDITY:") for reference in references)
    assert not any(reference.startswith("BDX:ENV:PRESSURE:") for reference in references)


def test_raspberry_environment_operator_links_to_full_temperature_history(
    tmp_path: Path,
):
    generate(RASPBERRY_PROFILE, tmp_path, only="environment")

    targets = _open_file_targets(tmp_path / "environment.bob")
    assert targets == {"environment_0_environment_temperatures.plt"}
    assert (tmp_path / "environment_0_environment_temperatures.plt").exists()


def test_generate_only_environment_does_not_overwrite_unrelated_displays(tmp_path: Path):
    psu_display = tmp_path / "psu.bob"
    overview_display = tmp_path / "overview.bob"
    psu_display.write_text("keep psu", encoding="utf-8")
    overview_display.write_text("keep overview", encoding="utf-8")

    generate(RASPBERRY_PROFILE, tmp_path, only="environment")

    assert ET.parse(tmp_path / "environment.bob").getroot().tag == "display"
    assert psu_display.read_text(encoding="utf-8") == "keep psu"
    assert overview_display.read_text(encoding="utf-8") == "keep overview"


def test_psu_operator_and_expert_displays_from_main_server_profile(tmp_path: Path):
    pvs = generate(MAIN_SERVER_PROFILE, tmp_path, only="psu")

    operator = tmp_path / "psu.bob"
    expert = tmp_path / "psu_expert.bob"
    assert ET.parse(operator).getroot().tag == "display"
    assert ET.parse(expert).getroot().tag == "display"
    assert "PV name" not in _labels(operator)
    assert "PV name" in _labels(expert)
    assert {pv.name for pv in pvs if pv.subsystem == "psu"}.issubset(_pv_references(expert))
    assert "BDX:PSU:LV1:CH1:VOLTAGE_REQUEST" in _pv_references(operator)
    assert "BDX:PSU:LV1:CH1:CURRENT_LIMIT_REQUEST" in _pv_references(operator)
    assert "BDX:PSU:LV1:CH1:APPLY_CMD" in _pv_references(operator)
    assert "BDX:PSU:LV1:CH1:OVP_SET" not in _pv_references(operator)
    assert "BDX:PSU:LV1:CH1:OCP_SET" not in _pv_references(operator)


def test_psu_operator_output_and_all_off_actions_are_confirmed(tmp_path: Path):
    generate(MAIN_SERVER_PROFILE, tmp_path, only="psu")
    operator = tmp_path / "psu.bob"

    assert _confirmed_button_texts_for_pv(
        operator,
        "BDX:PSU:LV1:CH1:OUTPUT_SET",
    ) == {"ON", "OFF"}
    assert _confirmed_button_texts_for_pv(
        operator,
        "BDX:PSU:LV2:CH2:OUTPUT_SET",
    ) == {"ON", "OFF"}
    assert _confirmed_button_texts_for_pv(operator, "BDX:PSU:LV1:ALLOFF_CMD") == {
        "ALL OFF"
    }
    assert _confirmed_button_texts_for_pv(operator, "BDX:PSU:LV2:ALLOFF_CMD") == {
        "ALL OFF"
    }


def test_psu_generates_one_dual_axis_actual_readback_plot_per_supply(tmp_path: Path):
    generate(MAIN_SERVER_PROFILE, tmp_path, only="psu")

    plot_files = sorted(tmp_path.glob("psu_*.plt"))
    assert len(plot_files) == 2
    for path in plot_files:
        root = _plt(path)
        assert [axis.text for axis in root.findall("axes/axis/name")] == [
            "Voltage [V]",
            "Current [A]",
        ]
        assert [axis.text for axis in root.findall("axes/axis/right")] == [
            "false",
            "true",
        ]
        trace_names = _plt_traces(root)
        assert all(name.endswith(("VOLTAGE_RBV", "CURRENT_RBV")) for name in trace_names)
        assert len(trace_names) == 4
        assert {element.text for element in root.findall("pvlist/pv/axis")} == {"0", "1"}


def test_psu_operator_links_to_full_historical_databrowser_resources(tmp_path: Path):
    generate(MAIN_SERVER_PROFILE, tmp_path, only="psu")

    targets = _open_file_targets(tmp_path / "psu.bob")
    assert targets == {
        "psu_0_lv1_actual_voltage_and_current.plt",
        "psu_1_lv2_actual_voltage_and_current.plt",
    }
    for target in targets:
        assert (tmp_path / target).exists()


def test_generate_only_psu_does_not_overwrite_unrelated_displays(tmp_path: Path):
    chiller_display = tmp_path / "chiller.bob"
    environment_display = tmp_path / "environment.bob"
    chiller_display.write_text("keep chiller", encoding="utf-8")
    environment_display.write_text("keep environment", encoding="utf-8")

    generate(MAIN_SERVER_PROFILE, tmp_path, only="psu")

    assert ET.parse(tmp_path / "psu.bob").getroot().tag == "display"
    assert ET.parse(tmp_path / "psu_expert.bob").getroot().tag == "display"
    assert chiller_display.read_text(encoding="utf-8") == "keep chiller"
    assert environment_display.read_text(encoding="utf-8") == "keep environment"


def test_chiller_operator_and_expert_displays_from_main_server_profile(tmp_path: Path):
    pvs = generate(MAIN_SERVER_PROFILE, tmp_path, only="chiller")

    operator = tmp_path / "chiller.bob"
    expert = tmp_path / "chiller_expert.bob"
    assert ET.parse(operator).getroot().tag == "display"
    assert ET.parse(expert).getroot().tag == "display"
    assert "PV name" not in _labels(operator)
    assert "PV name" in _labels(expert)
    assert {pv.name for pv in pvs if pv.subsystem == "chiller"}.issubset(
        _pv_references(expert)
    )
    assert "BDX:CHILLER:CHILLER1:SETPOINT_REQUEST" in _pv_references(operator)
    assert "BDX:CHILLER:CHILLER1:APPLY_SETPOINT_CMD" in _pv_references(operator)
    assert "BDX:CHILLER:CHILLER1:PRESSURE_RBV" not in _pv_references(operator)
    assert "BDX:CHILLER:CHILLER1:EXTERNAL_TEMPERATURE_RBV" not in _pv_references(operator)


def test_chiller_operator_start_stop_actions_are_confirmed(tmp_path: Path):
    generate(MAIN_SERVER_PROFILE, tmp_path, only="chiller")

    assert _confirmed_button_texts_for_pv(
        tmp_path / "chiller.bob",
        "BDX:CHILLER:CHILLER1:RUN_SET",
    ) == {"START", "STOP"}


def test_chiller_temperature_plot_excludes_duplicate_and_disabled_measurements(
    tmp_path: Path,
):
    generate(MAIN_SERVER_PROFILE, tmp_path, only="chiller")

    plot_files = sorted(tmp_path.glob("chiller_*.plt"))
    assert len(plot_files) == 1
    traces = _plt_traces(_plt(plot_files[0]))
    assert traces == {
        "BDX:CHILLER:CHILLER1:CONTROLLED_TEMPERATURE_RBV",
        "BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV",
        "BDX:CHILLER:CHILLER1:SETPOINT_RBV",
    }


def test_chiller_operator_links_to_full_historical_databrowser_resource(tmp_path: Path):
    generate(MAIN_SERVER_PROFILE, tmp_path, only="chiller")

    targets = _open_file_targets(tmp_path / "chiller.bob")
    assert targets == {"chiller_0_chiller_temperature.plt"}
    assert (tmp_path / "chiller_0_chiller_temperature.plt").exists()


def test_generate_only_chiller_does_not_overwrite_unrelated_displays(tmp_path: Path):
    psu_display = tmp_path / "psu.bob"
    environment_display = tmp_path / "environment.bob"
    psu_display.write_text("keep psu", encoding="utf-8")
    environment_display.write_text("keep environment", encoding="utf-8")

    generate(MAIN_SERVER_PROFILE, tmp_path, only="chiller")

    assert ET.parse(tmp_path / "chiller.bob").getroot().tag == "display"
    assert ET.parse(tmp_path / "chiller_expert.bob").getroot().tag == "display"
    assert psu_display.read_text(encoding="utf-8") == "keep psu"
    assert environment_display.read_text(encoding="utf-8") == "keep environment"


def test_phoebus_launcher_uses_live_only_databrowser_settings_when_archive_disabled(
    tmp_path: Path,
):
    launcher = tmp_path / "fake_phoebus.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)

    env = {
        **os.environ,
        "BDX_PHOEBUS_CMD": str(launcher),
        "BDX_PHOEBUS_ENV": str(tmp_path / "missing.env"),
        "BDX_ARCHIVER_ENABLED": "false",
        "XDG_RUNTIME_DIR": str(tmp_path),
    }
    completed = subprocess.run(
        ["bash", "scripts/launch_phoebus.sh", "environment"],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    settings = (tmp_path / "bdx-phoebus" / "settings.ini").read_text(
        encoding="utf-8"
    )
    assert "org.csstudio.trends.databrowser3/urls=\n" in settings
    assert "org.csstudio.trends.databrowser3/archives=\n" in settings
    assert "org.csstudio.trends.databrowser3/use_default_archives=true\n" in settings
    assert "org.csstudio.trends.databrowser3/drop_failed_archives=true\n" in settings
    assert "pbraw://" not in settings
    assert "Archiver enabled: false" in completed.stdout
    assert not (tmp_path / "all_pvs.bob").exists()


def test_phoebus_launcher_configures_archive_settings_when_enabled(tmp_path: Path):
    launcher = tmp_path / "fake_phoebus.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)

    env = {
        **os.environ,
        "BDX_PHOEBUS_CMD": str(launcher),
        "BDX_PHOEBUS_ENV": str(tmp_path / "missing.env"),
        "BDX_ARCHIVER_ENABLED": "true",
        "BDX_ARCHIVER_URL": "http://operator:secret@archiver.example:17668/retrieval",
        "BDX_ARCHIVER_NAME": "BDX Prototype",
        "BDX_ARCHIVER_STRICT_CHECK": "false",
        "XDG_RUNTIME_DIR": str(tmp_path),
    }
    completed = subprocess.run(
        ["bash", "scripts/launch_phoebus.sh", "psu"],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    settings = (tmp_path / "bdx-phoebus" / "settings.ini").read_text(
        encoding="utf-8"
    )
    expected = "pbraw://archiver.example:17668/retrieval|BDX Prototype"
    assert f"org.csstudio.trends.databrowser3/urls={expected}\n" in settings
    assert f"org.csstudio.trends.databrowser3/archives={expected}\n" in settings
    assert "org.phoebus.archive.reader.appliance/useHttps=false\n" in settings
    assert "secret" not in settings
    assert "secret" not in completed.stdout
    assert "Archiver enabled: true" in completed.stdout
    assert "Archiver retrieval: pbraw://archiver.example:17668/retrieval" in completed.stdout


def test_phoebus_launcher_keeps_live_fallback_when_archive_preflight_fails(
    tmp_path: Path,
):
    launcher = tmp_path / "fake_phoebus.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)

    env = {
        **os.environ,
        "BDX_PHOEBUS_CMD": str(launcher),
        "BDX_PHOEBUS_ENV": str(tmp_path / "missing.env"),
        "BDX_ARCHIVER_ENABLED": "true",
        "BDX_ARCHIVER_URL": "http://127.0.0.1:9/retrieval",
        "BDX_ARCHIVER_STRICT_CHECK": "false",
        "BDX_ARCHIVER_PREFLIGHT_PV": "BDX:ENV:TEMP:T00:VALUE",
        "XDG_RUNTIME_DIR": str(tmp_path),
    }
    completed = subprocess.run(
        ["bash", "scripts/launch_phoebus.sh", "environment"],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert "Archiver enabled: true" in completed.stdout
    assert "live Channel Access fallback" in completed.stderr


def test_raspberry_overview_environment_chart_uses_configured_temperature_pvs(
    tmp_path: Path,
):
    generate(RASPBERRY_PROFILE, tmp_path, only="overview")

    databrowsers = _databrowsers(tmp_path / "overview.bob")
    environment_charts = [
        widget
        for widget in databrowsers
        if _plt(tmp_path / widget.findtext("file")).findtext("title")
        in ("Environment", "Environment temperatures")
    ]
    assert len(environment_charts) == 1
    traces = _plt_traces(_plt(tmp_path / environment_charts[0].findtext("file")))
    assert traces == RASPBERRY_TEMPERATURE_PVS
