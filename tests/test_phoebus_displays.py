from pathlib import Path
import os
import subprocess
import xml.etree.ElementTree as ET

from bdx_slow_control.phoebus_generator import generate


PROTOTYPE_PROFILE = Path("config/profiles/prototype")
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


def test_generated_databrowser_plt_files_do_not_configure_archive_data_sources(
    tmp_path: Path,
):
    """No archive appliance is deployed; a configured-but-unreachable archive
    data source can stall a Data Browser plot's live samples."""
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


def test_generate_only_environment_does_not_overwrite_unrelated_displays(tmp_path: Path):
    psu_display = tmp_path / "psu.bob"
    overview_display = tmp_path / "overview.bob"
    psu_display.write_text("keep psu", encoding="utf-8")
    overview_display.write_text("keep overview", encoding="utf-8")

    generate(RASPBERRY_PROFILE, tmp_path, only="environment")

    assert ET.parse(tmp_path / "environment.bob").getroot().tag == "display"
    assert psu_display.read_text(encoding="utf-8") == "keep psu"
    assert overview_display.read_text(encoding="utf-8") == "keep overview"


def test_phoebus_launcher_disables_default_databrowser_archives(tmp_path: Path):
    launcher = tmp_path / "fake_phoebus.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)

    env = {
        **os.environ,
        "BDX_PHOEBUS_CMD": str(launcher),
        "BDX_PHOEBUS_ENV": str(tmp_path / "missing.env"),
        "XDG_RUNTIME_DIR": str(tmp_path),
    }
    subprocess.run(
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
    assert "org.csstudio.trends.databrowser3/use_default_archives=false\n" in settings
    assert not (tmp_path / "all_pvs.bob").exists()


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
