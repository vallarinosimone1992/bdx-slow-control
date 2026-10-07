"""Phoebus generator wrapper adding notifier controls to the overview."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from . import phoebus_generator_core as core
from .phoebus_generator_core import *  # noqa: F401,F403


_ORIGINAL_GENERATE_OVERVIEW = core.generate_overview
NOTIFIER_ROW_Y = 150
NOTIFIER_ROW_HEIGHT = 64


def _next_widget_counter(root) -> int:
    count = 0
    for widget in root.findall("widget"):
        name = widget.findtext("name", "")
        try:
            count = max(count, int(name.rsplit("_", 1)[1]))
        except (IndexError, ValueError):
            continue
    return count


def _shift_overview_content(root, *, start_y: int, delta: int) -> None:
    for widget in root.findall("widget"):
        y_element = widget.find("y")
        if y_element is None or y_element.text is None:
            continue
        try:
            y = int(y_element.text)
        except ValueError:
            continue
        if y >= start_y:
            y_element.text = str(y + delta)
    height = root.find("height")
    if height is not None and height.text:
        height.text = str(int(height.text) + delta)


def _add_notifier_controls(path: Path) -> None:
    tree = core.ET.parse(path)
    root = tree.getroot()
    _shift_overview_content(
        root,
        start_y=NOTIFIER_ROW_Y,
        delta=NOTIFIER_ROW_HEIGHT,
    )

    display = core.Display.__new__(core.Display)
    display.root = root
    display._counter = _next_widget_counter(root)
    y = NOTIFIER_ROW_Y

    display.label("Notifier", 20, y + 4, 80, 28, size=14, bold=True)
    display.led(
        "BDX:GLOBAL:NOTIFIER_ONLINE",
        105,
        y + 4,
        26,
        26,
    )
    display.text_update(
        "BDX:GLOBAL:NOTIFIER_STATUS",
        140,
        y + 2,
        120,
        30,
        size=13,
        bold=True,
        background=(235, 235, 235),
    )
    display.label("Snooze [min]", 280, y + 4, 95, 28, size=12, bold=True)
    display.text_entry(
        "BDX:GLOBAL:NOTIFIER_SNOOZE_MINUTES",
        380,
        y + 2,
        80,
        30,
        precision=1,
        format_code=1,
    )
    display.action_button(
        "SNOOZE",
        "BDX:GLOBAL:NOTIFIER_SNOOZE_CMD",
        "On",
        475,
        y + 2,
        90,
        30,
        confirm="Temporarily disable BDX notifications for the selected number of minutes?",
        background=(255, 220, 145),
    )
    display.action_button(
        "RESUME",
        "BDX:GLOBAL:NOTIFIER_RESUME_CMD",
        "On",
        575,
        y + 2,
        90,
        30,
        background=(180, 225, 185),
    )
    display.label("Until", 690, y + 4, 42, 28, size=12, bold=True)
    display.text_update(
        "BDX:GLOBAL:NOTIFIER_SNOOZE_UNTIL",
        735,
        y + 2,
        220,
        30,
        size=11,
    )
    display.label("Last heartbeat", 975, y + 4, 105, 28, size=11, bold=True)
    display.text_update(
        "BDX:GLOBAL:NOTIFIER_LAST_HEARTBEAT",
        1085,
        y + 2,
        295,
        30,
        size=10,
    )

    tree.write(path, encoding="UTF-8", xml_declaration=True)


def generate_overview(
    pvs: Sequence[core.PVInfo],
    output: Path,
    groups: dict[str, list[core.TrendGroup]],
    navigation: Sequence[tuple[str, str]],
) -> None:
    _ORIGINAL_GENERATE_OVERVIEW(pvs, output, groups, navigation)
    _add_notifier_controls(output / "overview.bob")


# core.generate() looks up generate_overview in its own module namespace.
core.generate_overview = generate_overview


def main(argv: list[str] | None = None) -> int:
    return core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
