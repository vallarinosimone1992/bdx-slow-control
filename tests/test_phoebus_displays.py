from pathlib import Path
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


def _pv_references(path: Path) -> set[str]:
    root = ET.parse(path).getroot()
    references = set()
    for tag in ("pv_name", "y_pv"):
        for element in root.iter(tag):
            if element.text and element.text.startswith("BDX:"):
                references.add(element.text)
    return references


def _stripcharts(path: Path) -> list[ET.Element]:
    root = ET.parse(path).getroot()
    return [
        widget
        for widget in root.findall("widget")
        if widget.get("type") == "stripchart"
    ]


def _stripchart_traces(stripchart: ET.Element) -> set[str]:
    return {
        element.text
        for element in stripchart.iter("y_pv")
        if element.text and element.text.startswith("BDX:")
    }


def test_generated_displays_are_valid_xml_and_cover_every_pv(tmp_path: Path):
    pvs = generate(PROTOTYPE_PROFILE, tmp_path)
    for path in tmp_path.glob("*.bob"):
        assert ET.parse(path).getroot().tag == "display"

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


def test_raspberry_environment_display_groups_all_temperatures_in_one_stripchart(
    tmp_path: Path,
):
    generate(RASPBERRY_PROFILE, tmp_path, only="environment")

    stripcharts = _stripcharts(tmp_path / "environment.bob")
    temperature_charts = [
        stripchart
        for stripchart in stripcharts
        if RASPBERRY_TEMPERATURE_PVS.issubset(_stripchart_traces(stripchart))
    ]

    assert len(temperature_charts) == 1
    chart = temperature_charts[0]
    assert chart.findtext("title") == "Environment temperatures"
    assert chart.findtext("show_legend") == "true"
    assert chart.findtext("y_axes/y_axis/title") == "Temperature [degC]"
    assert chart.findtext("y_axes/y_axis/autoscale") == "true"
    assert _stripchart_traces(chart) == RASPBERRY_TEMPERATURE_PVS


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
    assert not (tmp_path / "all_pvs.bob").exists()


def test_raspberry_overview_environment_chart_uses_configured_temperature_pvs(
    tmp_path: Path,
):
    generate(RASPBERRY_PROFILE, tmp_path, only="overview")

    stripcharts = _stripcharts(tmp_path / "overview.bob")
    environment_charts = [
        stripchart
        for stripchart in stripcharts
        if stripchart.findtext("title") == "Environment"
        or stripchart.findtext("title") == "Environment temperatures"
    ]
    assert len(environment_charts) == 1
    traces = _stripchart_traces(environment_charts[0])
    assert traces == RASPBERRY_TEMPERATURE_PVS
