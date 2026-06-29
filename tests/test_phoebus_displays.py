from pathlib import Path
import xml.etree.ElementTree as ET

from bdx_slow_control.phoebus_generator import catalog, generate


def _pv_references(path: Path) -> set[str]:
    root = ET.parse(path).getroot()
    references = set()
    for tag in ("pv_name", "y_pv"):
        for element in root.iter(tag):
            if element.text and element.text.startswith("BDX:"):
                references.add(element.text)
    return references


def test_generated_displays_are_valid_xml_and_cover_every_pv(tmp_path: Path):
    pvs = generate(Path("config"), tmp_path)
    for path in tmp_path.glob("*.bob"):
        assert ET.parse(path).getroot().tag == "display"

    expected = {pv.name for pv in pvs}
    assert expected.issubset(_pv_references(tmp_path / "all_pvs.bob"))


def test_every_writable_pv_has_a_control_in_all_pvs(tmp_path: Path):
    pvs = generate(Path("config"), tmp_path)
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
    pvs = generate(Path("config"), tmp_path)
    expected = {pv.name for pv in pvs}
    for path in tmp_path.glob("*.bob"):
        root = ET.parse(path).getroot()
        assert _pv_references(path).issubset(expected)
        for file_element in root.iter("file"):
            if file_element.text and file_element.text.endswith(".bob"):
                assert (tmp_path / file_element.text).exists()
