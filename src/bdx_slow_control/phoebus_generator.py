"""Generate Phoebus Display Builder files from the configured PV database."""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
import xml.etree.ElementTree as ET

from .prototype import build_prototype

DEFAULT_TREND_RANGE = "10 minutes"
DEFAULT_TREND_SCAN_PERIOD = 5.0


def trend_range() -> str:
    """Live trend window, configurable through the BDX_TREND_RANGE environment variable."""
    return os.environ.get("BDX_TREND_RANGE", DEFAULT_TREND_RANGE)


def trend_scan_period() -> float:
    """Live trend scan period used to timestamp Data Browser samples."""
    return float(os.environ.get("BDX_TREND_SCAN_PERIOD", DEFAULT_TREND_SCAN_PERIOD))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


@dataclass(frozen=True)
class PVInfo:
    name: str
    subsystem: str
    read_only: bool
    kind: str
    command: bool


@dataclass(frozen=True)
class TraceInfo:
    pv: str
    label: str


NAVIGATION = [
    ("Overview", "overview.bob"),
    ("PSU", "psu.bob"),
    ("Chiller", "chiller.bob"),
    ("Environment", "environment.bob"),
    ("HV", "hv.bob"),
    ("DAQ", "daq.bob"),
    ("Global", "global.bob"),
    ("Trends", "trends.bob"),
    ("All PVs", "all_pvs.bob"),
]

GENERATABLE_TARGETS = (
    "overview",
    "psu",
    "chiller",
    "environment",
    "hv",
    "daq",
    "global",
    "trends",
    "all-pvs",
)

PALETTE = [
    (33, 113, 181),
    (230, 85, 13),
    (49, 163, 84),
    (117, 107, 177),
    (231, 41, 138),
    (166, 86, 40),
    (0, 153, 153),
    (102, 102, 102),
]


class Display:
    """Small XML builder for Phoebus Display Builder files."""

    def __init__(self, name: str, width: int, height: int) -> None:
        self.root = ET.Element("display", {"version": "2.0.0"})
        ET.SubElement(self.root, "name").text = name
        ET.SubElement(self.root, "width").text = str(width)
        ET.SubElement(self.root, "height").text = str(height)
        self._counter = 0

    def widget(
        self,
        widget_type: str,
        version: str,
        name: str,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> ET.Element:
        self._counter += 1
        widget = ET.SubElement(
            self.root,
            "widget",
            {"type": widget_type, "version": version},
        )
        ET.SubElement(widget, "name").text = f"{name}_{self._counter}"
        ET.SubElement(widget, "x").text = str(x)
        ET.SubElement(widget, "y").text = str(y)
        ET.SubElement(widget, "width").text = str(width)
        ET.SubElement(widget, "height").text = str(height)
        return widget

    def label(
        self,
        text: str,
        x: int,
        y: int,
        width: int,
        height: int = 24,
        *,
        size: float = 14.0,
        bold: bool = False,
        background: tuple[int, int, int] | None = None,
        foreground: tuple[int, int, int] | None = None,
    ) -> ET.Element:
        widget = self.widget("label", "2.0.0", "Label", x, y, width, height)
        ET.SubElement(widget, "text").text = text
        font = ET.SubElement(widget, "font")
        ET.SubElement(
            font,
            "font",
            {
                "name": "Default",
                "family": "Liberation Sans",
                "style": "BOLD" if bold else "REGULAR",
                "size": f"{size:.1f}",
            },
        )
        if background is not None:
            color = ET.SubElement(widget, "background_color")
            ET.SubElement(
                color,
                "color",
                {"red": str(background[0]), "green": str(background[1]), "blue": str(background[2])},
            )
            ET.SubElement(widget, "transparent").text = "false"
        if foreground is not None:
            color = ET.SubElement(widget, "foreground_color")
            ET.SubElement(
                color,
                "color",
                {"red": str(foreground[0]), "green": str(foreground[1]), "blue": str(foreground[2])},
            )
        return widget

    def text_update(
        self,
        pv: str,
        x: int,
        y: int,
        width: int,
        height: int = 25,
        *,
        size: float | None = None,
        bold: bool = False,
        precision: int | None = None,
        format_code: int | None = None,
        show_units: bool | None = None,
        background: tuple[int, int, int] | None = None,
        foreground: tuple[int, int, int] | None = None,
    ) -> ET.Element:
        widget = self.widget("textupdate", "2.0.0", "Readback", x, y, width, height)
        ET.SubElement(widget, "pv_name").text = pv
        if size is not None:
            font = ET.SubElement(widget, "font")
            ET.SubElement(
                font,
                "font",
                {
                    "name": "Default",
                    "family": "Liberation Sans",
                    "style": "BOLD" if bold else "REGULAR",
                    "size": f"{size:.1f}",
                },
            )
        if format_code is not None:
            ET.SubElement(widget, "format").text = str(format_code)
        if precision is not None:
            ET.SubElement(widget, "precision").text = str(precision)
        if show_units is not None:
            ET.SubElement(widget, "show_units").text = str(show_units).lower()
        if background is not None:
            color = ET.SubElement(widget, "background_color")
            ET.SubElement(
                color,
                "color",
                {"red": str(background[0]), "green": str(background[1]), "blue": str(background[2])},
            )
            ET.SubElement(widget, "transparent").text = "false"
        if foreground is not None:
            color = ET.SubElement(widget, "foreground_color")
            ET.SubElement(
                color,
                "color",
                {"red": str(foreground[0]), "green": str(foreground[1]), "blue": str(foreground[2])},
            )
        return widget

    def text_entry(self, pv: str, x: int, y: int, width: int, height: int = 25) -> ET.Element:
        widget = self.widget("textentry", "2.0.0", "Setpoint", x, y, width, height)
        ET.SubElement(widget, "pv_name").text = pv
        return widget

    def bool_button(
        self,
        pv: str,
        x: int,
        y: int,
        width: int,
        height: int = 28,
        off_label: str = "OFF",
        on_label: str = "ON",
    ) -> ET.Element:
        widget = self.widget("bool_button", "2.0.0", "Boolean", x, y, width, height)
        ET.SubElement(widget, "pv_name").text = pv
        ET.SubElement(widget, "off_label").text = off_label
        ET.SubElement(widget, "on_label").text = on_label
        return widget

    def led(
        self,
        pv: str,
        x: int,
        y: int,
        width: int = 26,
        height: int = 26,
        *,
        off_color: tuple[int, int, int] = (180, 40, 40),
        on_color: tuple[int, int, int] = (40, 170, 80),
    ) -> ET.Element:
        widget = self.widget("led", "2.0.0", "StatusLED", x, y, width, height)
        ET.SubElement(widget, "pv_name").text = pv
        off = ET.SubElement(widget, "off_color")
        ET.SubElement(
            off,
            "color",
            {"red": str(off_color[0]), "green": str(off_color[1]), "blue": str(off_color[2])},
        )
        on = ET.SubElement(widget, "on_color")
        ET.SubElement(
            on,
            "color",
            {"red": str(on_color[0]), "green": str(on_color[1]), "blue": str(on_color[2])},
        )
        ET.SubElement(widget, "border_alarm_sensitive").text = "false"
        return widget

    def action_button(
        self,
        text: str,
        pv: str,
        value: str,
        x: int,
        y: int,
        width: int,
        height: int = 30,
        *,
        confirm: str | None = None,
        background: tuple[int, int, int] | None = None,
    ) -> ET.Element:
        widget = self.widget("action_button", "3.0.0", "Action", x, y, width, height)
        ET.SubElement(widget, "pv_name").text = pv
        actions = ET.SubElement(widget, "actions")
        action = ET.SubElement(actions, "action", {"type": "write_pv"})
        ET.SubElement(action, "pv_name").text = pv
        ET.SubElement(action, "value").text = value
        ET.SubElement(action, "description").text = f"Write {value} to {pv}"
        ET.SubElement(widget, "text").text = text
        ET.SubElement(widget, "tooltip").text = "$(actions)"
        if confirm:
            ET.SubElement(widget, "confirm_dialog").text = "true"
            ET.SubElement(widget, "confirm_message").text = confirm
        if background is not None:
            color = ET.SubElement(widget, "background_color")
            ET.SubElement(
                color,
                "color",
                {"red": str(background[0]), "green": str(background[1]), "blue": str(background[2])},
            )
        return widget

    def open_button(
        self,
        text: str,
        filename: str,
        x: int,
        y: int,
        width: int,
        height: int = 30,
    ) -> ET.Element:
        widget = self.widget("action_button", "3.0.0", "Navigation", x, y, width, height)
        actions = ET.SubElement(widget, "actions")
        action = ET.SubElement(actions, "action", {"type": "open_display"})
        ET.SubElement(action, "file").text = filename
        ET.SubElement(action, "target").text = "replace"
        ET.SubElement(action, "description").text = f"Open {text}"
        ET.SubElement(widget, "text").text = text
        ET.SubElement(widget, "tooltip").text = "$(actions)"
        return widget

    def databrowser(
        self,
        plt_filename: str,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> ET.Element:
        widget = self.widget("databrowser", "2.0.0", "DataBrowser", x, y, width, height)
        ET.SubElement(widget, "file").text = plt_filename
        ET.SubElement(widget, "show_toolbar").text = "true"
        return widget

    def write(self, path: Path) -> None:
        ET.indent(self.root, space="  ")
        tree = ET.ElementTree(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tree.write(path, encoding="UTF-8", xml_declaration=True)


def catalog(config_dir: Path) -> list[PVInfo]:
    pvdb, _ = build_prototype(config_dir)
    result = []
    for name, channel in sorted(pvdb.items()):
        class_name = type(channel).__name__
        subsystem = name.split(":", 2)[1].lower()
        if subsystem == "env":
            subsystem = "environment"
        read_only = class_name.endswith("RO")
        if "String" in class_name:
            kind = "string"
        elif "BoolEnum" in class_name:
            kind = "bool"
        elif "Integer" in class_name:
            kind = "integer"
        else:
            kind = "float"
        result.append(
            PVInfo(
                name=name,
                subsystem=subsystem,
                read_only=read_only,
                kind=kind,
                command=name.endswith("_CMD"),
            )
        )
    return result


def add_header(display: Display, title: str) -> None:
    display.label(title, 20, 12, 650, 38, size=24, bold=True)
    x = 20
    for text, filename in NAVIGATION:
        display.open_button(text, filename, x, 58, 112, 30)
        x += 118


def add_pv_table(
    display: Display,
    pvs: Sequence[PVInfo],
    start_y: int,
    *,
    include_heading: bool = True,
) -> int:
    y = start_y
    if include_heading:
        display.label("PV name", 20, y, 570, 26, bold=True, background=(220, 228, 236))
        display.label("Current value", 600, y, 250, 26, bold=True, background=(220, 228, 236))
        display.label("Control", 870, y, 270, 26, bold=True, background=(220, 228, 236))
        y += 32

    for index, pv in enumerate(pvs):
        background = (247, 249, 251) if index % 2 == 0 else (235, 240, 244)
        display.label(pv.name, 20, y, 570, 25, size=11, background=background)
        display.text_update(pv.name, 600, y, 250, 25)
        if not pv.read_only:
            if pv.command:
                confirm = None
                if pv.name.endswith("ALLOFF_CMD"):
                    confirm = f"Execute all-off command {pv.name}?"
                elif pv.name.endswith("INTERLOCK_TEST_CMD"):
                    confirm = "Trigger the simulated global interlock and switch power outputs off?"
                display.action_button(
                    "EXECUTE",
                    pv.name,
                    "1",
                    870,
                    y,
                    135,
                    25,
                    confirm=confirm,
                    background=(255, 205, 120) if confirm else None,
                )
            elif pv.kind == "bool":
                display.bool_button(pv.name, 870, y, 135, 25)
            else:
                display.text_entry(pv.name, 870, y, 270, 25)
        y += 29
    return y


@dataclass(frozen=True)
class TrendGroup:
    title: str
    traces: list[TraceInfo]
    y_axis_title: str = "Value"


def trace_label(pv_name: str) -> str:
    parts = pv_name.split(":")
    if len(parts) >= 2 and parts[-1] == "VALUE":
        return parts[-2]
    return pv_name


def traces_for_pvs(pv_names: Sequence[str], *, concise_value_labels: bool = False) -> list[TraceInfo]:
    return [
        TraceInfo(pv=name, label=trace_label(name) if concise_value_labels else name)
        for name in pv_names
    ]


def temperature_traces(groups: Sequence[TrendGroup]) -> list[TraceInfo]:
    for group in groups:
        if group.title == "Environment temperatures":
            return group.traces
    return []


def status_pv_for_value(value_pv: str) -> str:
    return value_pv.removesuffix(":VALUE") + ":STATUS"


def status_ok_pv_for_value(value_pv: str) -> str:
    return value_pv.removesuffix(":VALUE") + ":STATUS_OK"


def temperature_summary_height(sensor_count: int) -> int:
    if sensor_count == 0:
        return 0
    rows = (sensor_count + 3) // 4
    return 42 + rows * 115


def add_environment_health(
    display: Display,
    pvs: Sequence[PVInfo],
    y: int,
) -> int:
    names = {pv.name for pv in pvs}
    if "BDX:ENV:HEARTBEAT" not in names and "BDX:ENV:LAST_TEMPERATURE_UPDATE" not in names:
        return y

    display.label("Environment slow control", 20, y, 300, 30, size=18, bold=True)
    display.label("Heartbeat", 350, y + 3, 88, 24, size=12, bold=True)
    if "BDX:ENV:HEARTBEAT" in names:
        display.text_update("BDX:ENV:HEARTBEAT", 442, y, 100, 28, size=13)
    display.label("Last temperature update", 585, y + 3, 190, 24, size=12, bold=True)
    if "BDX:ENV:LAST_TEMPERATURE_UPDATE" in names:
        display.text_update("BDX:ENV:LAST_TEMPERATURE_UPDATE", 780, y, 260, 28, size=13)
    display.open_button("Expert PVs", "environment_expert.bob", 1190, y, 150, 30)
    return y + 52


def add_temperature_summary(display: Display, traces: Sequence[TraceInfo], y: int) -> int:
    if not traces:
        return y

    display.label("Current temperatures", 20, y, 340, 30, size=18, bold=True)
    y += 40

    card_width = 320
    card_height = 92
    column_gap = 20
    row_gap = 23
    for index, trace in enumerate(traces):
        row = index // 4
        column = index % 4
        x = 20 + column * (card_width + column_gap)
        card_y = y + row * (card_height + row_gap)
        color = PALETTE[index % len(PALETTE)]
        pale_color = (
            min(255, color[0] + 205),
            min(255, color[1] + 205),
            min(255, color[2] + 205),
        )

        display.label("", x, card_y, card_width, card_height, background=(245, 248, 250))
        display.label(trace.label, x + 12, card_y + 10, 56, 28, size=16, bold=True, background=color, foreground=(255, 255, 255))
        display.led(status_ok_pv_for_value(trace.pv), x + 12, card_y + 52, 30, 30)
        display.text_update(
            trace.pv,
            x + 78,
            card_y + 6,
            145,
            46,
            size=28,
            bold=True,
            precision=2,
            format_code=1,
            show_units=False,
            background=pale_color,
        )
        display.label("°C", x + 232, card_y + 17, 34, 28, size=16, bold=True)
        display.label("Status", x + 52, card_y + 56, 58, 22, size=11, bold=True)

    return y + ((len(traces) + 3) // 4) * (card_height + row_gap)


def _environment_group(
    names: set[str],
    prefix: str,
    title: str,
    y_axis_title: str,
) -> TrendGroup | None:
    traces = sorted(
        name
        for name in names
        if name.startswith(prefix) and name.endswith(":VALUE")
    )
    if not traces:
        return None
    return TrendGroup(title, traces_for_pvs(traces, concise_value_labels=True), y_axis_title)


def environment_overview_group(pvs: Sequence[PVInfo]) -> TrendGroup | None:
    """Build an overview environment chart from the PVs present in this profile."""
    names = {pv.name for pv in pvs}
    traces = sorted(
        name
        for prefix in ("BDX:ENV:TEMP:", "BDX:ENV:HUMIDITY:", "BDX:ENV:PRESSURE:")
        for name in names
        if name.startswith(prefix) and name.endswith(":VALUE")
    )
    if not traces:
        return None

    axis_title = "Value"
    if all(name.startswith("BDX:ENV:TEMP:") for name in traces):
        axis_title = "Temperature [degC]"
    elif all(name.startswith("BDX:ENV:HUMIDITY:") for name in traces):
        axis_title = "Humidity [%]"
    elif all(name.startswith("BDX:ENV:PRESSURE:") for name in traces):
        axis_title = "Pressure"
    return TrendGroup("Environment", traces_for_pvs(traces, concise_value_labels=True), axis_title)


def trend_groups(pvs: Sequence[PVInfo]) -> dict[str, list[TrendGroup]]:
    names = {pv.name for pv in pvs}
    groups: dict[str, list[TrendGroup]] = {
        "psu": [],
        "chiller": [],
        "environment": [],
        "hv": [],
        "daq": [],
        "global": [],
    }

    for subsystem in ("PSU", "HV"):
        prefix = f"BDX:{subsystem}:"
        voltage = sorted(
            name for name in names if name.startswith(prefix) and name.endswith(("VOLTAGE_RBV", "OVP_RBV"))
        )
        current = sorted(
            name
            for name in names
            if name.startswith(prefix)
            and name.endswith(("CURRENT_RBV", "CURRENT_LIMIT_RBV", "OCP_RBV"))
        )
        key = subsystem.lower()
        if voltage:
            groups[key].append(TrendGroup(f"{subsystem} voltage and protection", traces_for_pvs(voltage)))
        if current:
            groups[key].append(TrendGroup(f"{subsystem} current and protection", traces_for_pvs(current)))

    chiller_temp = sorted(
        name
        for name in names
        if name.startswith("BDX:CHILLER:") and name.endswith(("TEMPERATURE_RBV", "SETPOINT_RBV"))
    )
    chiller_pressure = sorted(
        name for name in names if name.startswith("BDX:CHILLER:") and name.endswith("PRESSURE_RBV")
    )
    if chiller_temp:
        groups["chiller"].append(TrendGroup("Chiller temperature", traces_for_pvs(chiller_temp)))
    if chiller_pressure:
        groups["chiller"].append(TrendGroup("Chiller pressure", traces_for_pvs(chiller_pressure)))

    environment_groups = (
        _environment_group(
            names,
            "BDX:ENV:TEMP:",
            "Environment temperatures",
            "Temperature [degC]",
        ),
        _environment_group(
            names,
            "BDX:ENV:HUMIDITY:",
            "Environment humidity",
            "Humidity [%]",
        ),
        _environment_group(
            names,
            "BDX:ENV:PRESSURE:",
            "Environment pressure",
            "Pressure",
        ),
    )
    groups["environment"].extend(group for group in environment_groups if group is not None)

    if "BDX:GLOBAL:UPDATE_FREQUENCY_RBV" in names:
        groups["global"].append(
            TrendGroup(
                "Prototype update frequency",
                traces_for_pvs(["BDX:GLOBAL:UPDATE_FREQUENCY_RBV"]),
            )
        )
    return groups


def add_timing_controls(display: Display, y: int) -> int:
    display.label("IOC update period", 20, y, 190, 28, bold=True)
    display.text_entry("BDX:GLOBAL:UPDATE_PERIOD_SET", 220, y, 120, 28)
    display.label("s", 345, y, 30, 28)
    x = 390
    for period in (2, 5, 10, 30):
        display.action_button(
            f"{period} s",
            "BDX:GLOBAL:UPDATE_PERIOD_SET",
            str(period),
            x,
            y,
            75,
            28,
        )
        x += 82
    display.label("Frequency", 735, y, 95, 28, bold=True)
    display.text_update("BDX:GLOBAL:UPDATE_FREQUENCY_RBV", 835, y, 100, 28)
    display.label("Hz", 940, y, 35, 28)
    display.label("Allowed period: 2–3600 s; all values remain below 1 Hz.", 995, y, 390, 28)
    return y + 42


def generate_overview(pvs: Sequence[PVInfo], output: Path) -> None:
    display = Display("BDX Slow Control Overview", 1400, 980)
    add_header(display, "BDX prototype slow control")

    y = 105
    display.label("Global state", 20, y, 160, 28, bold=True)
    display.text_update("BDX:GLOBAL:SYSTEM_STATE", 185, y, 150, 28)
    display.label("Ready", 350, y, 70, 28, bold=True)
    display.text_update("BDX:GLOBAL:READY", 425, y, 90, 28)
    display.label("Interlock", 530, y, 90, 28, bold=True)
    display.text_update("BDX:GLOBAL:INTERLOCK_ACTIVE", 625, y, 90, 28)
    display.text_update("BDX:GLOBAL:INTERLOCK_REASON", 725, y, 340, 28)
    display.action_button(
        "TEST INTERLOCK",
        "BDX:GLOBAL:INTERLOCK_TEST_CMD",
        "1",
        1080,
        y,
        135,
        28,
        confirm="Trigger the simulated interlock and switch PSU/HV outputs off?",
        background=(255, 205, 120),
    )
    display.action_button(
        "RESET",
        "BDX:GLOBAL:INTERLOCK_RESET_CMD",
        "1",
        1225,
        y,
        75,
        28,
    )
    display.action_button(
        "ALL OFF",
        "BDX:GLOBAL:ALLOFF_CMD",
        "1",
        1310,
        y,
        75,
        28,
        confirm="Switch all simulated PSU and HV channels off?",
        background=(255, 150, 150),
    )
    y = add_timing_controls(display, y + 45)

    display.label("Subsystem communication", 20, y, 300, 28, size=18, bold=True)
    y += 35
    comm_pvs = [pv for pv in pvs if pv.name.endswith(":COMM_STATUS")]
    x = 20
    for pv in comm_pvs:
        short = pv.name.replace("BDX:", "").replace(":COMM_STATUS", "")
        display.label(short, x, y, 160, 24, size=11)
        display.text_update(pv.name, x, y + 25, 160, 25)
        x += 175
        if x > 1220:
            x = 20
            y += 58
    y += 70

    environment_group = environment_overview_group(pvs)
    if environment_group:
        plt_filename = f"overview_{slug(environment_group.title)}.plt"
        write_databrowser_plt(
            output / plt_filename,
            environment_group.title,
            environment_group.traces,
            environment_group.y_axis_title,
        )
        display.databrowser(plt_filename, 20, y, 670, 360)

    names = {pv.name for pv in pvs}
    chiller_traces = [
        name
        for name in (
            "BDX:CHILLER:CHILLER1:TEMPERATURE_RBV",
            "BDX:CHILLER:CHILLER1:SETPOINT_RBV",
            "BDX:CHILLER:CHILLER1:PRESSURE_RBV",
        )
        if name in names
    ]
    if chiller_traces:
        write_databrowser_plt(
            output / "overview_chiller.plt",
            "Chiller",
            traces_for_pvs(chiller_traces),
            "Value",
        )
        display.databrowser("overview_chiller.plt", 710, y, 670, 360)
    display.write(output / "overview.bob")


def generate_subsystem(
    subsystem: str,
    pvs: Sequence[PVInfo],
    groups: dict[str, list[TrendGroup]],
    output: Path,
) -> None:
    selected = [pv for pv in pvs if pv.subsystem == subsystem]
    chart_groups = groups.get(subsystem, [])
    summary_traces = temperature_traces(chart_groups) if subsystem == "environment" else []
    health_height = 52 if subsystem == "environment" and any(
        pv.name in {"BDX:ENV:HEARTBEAT", "BDX:ENV:LAST_TEMPERATURE_UPDATE"}
        for pv in selected
    ) else 0
    summary_height = temperature_summary_height(len(summary_traces))
    chart_rows = (len(chart_groups) + 1) // 2
    plot_height = chart_rows * 330
    control_height = 100 if subsystem == "global" else 50 if subsystem == "daq" else 0
    table_height = 0 if subsystem == "environment" else 90 + len(selected) * 29
    table_start = 118 + control_height + health_height + summary_height + plot_height
    height = table_start + table_height + 30
    display = Display(f"BDX {subsystem.upper()}", 1400, max(760, height))
    add_header(display, f"BDX {subsystem.upper()}")

    y = 105
    if subsystem == "global":
        y = add_timing_controls(display, y)
        display.action_button(
            "TEST INTERLOCK",
            "BDX:GLOBAL:INTERLOCK_TEST_CMD",
            "1",
            20,
            y,
            160,
            32,
            confirm="Trigger the simulated interlock and switch PSU/HV outputs off?",
            background=(255, 205, 120),
        )
        display.action_button(
            "RESET INTERLOCK",
            "BDX:GLOBAL:INTERLOCK_RESET_CMD",
            "1",
            190,
            y,
            160,
            32,
        )
        display.action_button(
            "GLOBAL ALL OFF",
            "BDX:GLOBAL:ALLOFF_CMD",
            "1",
            360,
            y,
            160,
            32,
            confirm="Switch all simulated PSU and HV channels off?",
            background=(255, 150, 150),
        )
        y += 50
    elif subsystem == "daq":
        for index, state in enumerate(("OFF", "STANDBY", "CONFIGURED", "RUNNING")):
            display.action_button(
                state,
                "BDX:DAQ:CRATE01:STATE_SET",
                state,
                20 + index * 125,
                y,
                115,
                30,
            )
        y += 45

    if subsystem == "environment":
        y = add_environment_health(display, selected, y)

    if summary_traces:
        y = add_temperature_summary(display, summary_traces, y)

    if chart_groups:
        for index, group in enumerate(chart_groups):
            column = index % 2
            row = index // 2
            plt_filename = f"{subsystem}_{index}_{slug(group.title)}.plt"
            write_databrowser_plt(output / plt_filename, group.title, group.traces, group.y_axis_title)
            display.databrowser(
                plt_filename,
                20 + column * 690,
                y + row * 330,
                670,
                310,
            )
        y += chart_rows * 330

    if subsystem != "environment":
        add_pv_table(display, selected, y + 10)
    display.write(output / f"{subsystem}.bob")
    if subsystem == "environment":
        generate_environment_expert(selected, output)


def generate_environment_expert(pvs: Sequence[PVInfo], output: Path) -> None:
    height = 170 + len(pvs) * 29
    display = Display("BDX ENVIRONMENT EXPERT", 1400, max(760, height))
    add_header(display, "BDX ENVIRONMENT EXPERT")
    display.open_button("Back to environment", "environment.bob", 20, 105, 210, 32)
    add_pv_table(display, pvs, 150)
    display.write(output / "environment_expert.bob")


def generate_trends(
    groups: dict[str, list[TrendGroup]],
    output: Path,
) -> None:
    all_groups: list[TrendGroup] = []
    for subsystem in ("environment", "chiller", "psu", "hv", "global"):
        all_groups.extend(groups.get(subsystem, []))
    rows = (len(all_groups) + 1) // 2
    display = Display("BDX Trends", 1400, 170 + rows * 330)
    add_header(display, "BDX live trends")
    y = add_timing_controls(display, 105)
    display.label(
        "Trend charts subscribe to live PV updates. Change the IOC update period above; "
        "use each chart toolbar to adjust its time range.",
        20,
        y,
        1320,
        28,
    )
    y += 38
    for index, group in enumerate(all_groups):
        column = index % 2
        row = index // 2
        plt_filename = f"trends_{index}_{slug(group.title)}.plt"
        write_databrowser_plt(output / plt_filename, group.title, group.traces, group.y_axis_title)
        display.databrowser(
            plt_filename,
            20 + column * 690,
            y + row * 330,
            670,
            310,
        )
    display.write(output / "trends.bob")


def generate_all_pvs(pvs: Sequence[PVInfo], output: Path) -> None:
    height = 145 + len(pvs) * 29
    display = Display("BDX All PVs", 1200, height)
    add_header(display, "BDX complete simulation PV table")
    add_pv_table(display, pvs, 105)
    display.write(output / "all_pvs.bob")


def write_databrowser_plt(
    path: Path,
    title: str,
    traces: Sequence[TraceInfo],
    y_axis_title: str,
) -> None:
    """Write a Data Browser *.plt file backing an embedded 'databrowser' widget.

    Traces intentionally omit <archive> data sources: this deployment has no
    archive appliance, and a configured-but-unreachable archive data source
    can stall the plot's live samples.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<databrowser>',
        f'    <title>{title}</title>',
        '    <save_changes>false</save_changes>',
        '    <show_legend>true</show_legend>',
        '    <show_toolbar>true</show_toolbar>',
        '    <grid>true</grid>',
        '    <update_period>1.0</update_period>',
        '    <scroll_step>5</scroll_step>',
        '    <scroll>true</scroll>',
        f'    <start>-{trend_range()}</start>',
        '    <end>now</end>',
        '    <archive_rescale>NONE</archive_rescale>',
        '    <axes>',
        '        <axis>',
        '            <visible>true</visible>',
        f'            <name>{y_axis_title}</name>',
        '            <use_axis_name>true</use_axis_name>',
        '            <use_trace_names>false</use_trace_names>',
        '            <right>false</right>',
        '            <min>0.0</min>',
        '            <max>100.0</max>',
        '            <grid>true</grid>',
        '            <autoscale>true</autoscale>',
        '            <log_scale>false</log_scale>',
        '        </axis>',
        '    </axes>',
        '    <annotations>',
        '    </annotations>',
        '    <pvlist>',
    ]
    for index, trace_info in enumerate(traces):
        rgb = PALETTE[index % len(PALETTE)]
        lines.extend(
            [
                '        <pv>',
                f'            <display_name>{trace_info.label}</display_name>',
                '            <visible>true</visible>',
                f'            <name>{trace_info.pv}</name>',
                '            <axis>0</axis>',
                '            <color>',
                f'                <red>{rgb[0]}</red>',
                f'                <green>{rgb[1]}</green>',
                f'                <blue>{rgb[2]}</blue>',
                '            </color>',
                '            <trace_type>SINGLE_LINE</trace_type>',
                '            <linewidth>2</linewidth>',
                '            <line_style>SOLID</line_style>',
                '            <point_type>NONE</point_type>',
                '            <point_size>6</point_size>',
                '            <waveform_index>0</waveform_index>',
                f'            <period>{trend_scan_period():.1f}</period>',
                '            <ring_size>5000</ring_size>',
                '            <request>RAW</request>',
                '        </pv>',
            ]
        )
    lines.extend(['    </pvlist>', '</databrowser>'])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_pv_table(pvs: Sequence[PVInfo], output: Path) -> None:
    pv_table = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        '<pvtable enable_save_restore="true" version="3.0">',
        '  <timeout>60.0</timeout>',
        '  <pvlist>',
    ]
    for pv in pvs:
        pv_table.extend(
            [
                '    <pv>',
                '      <selected>true</selected>',
                f'      <name>{pv.name}</name>',
                '      <tolerance>0.1</tolerance>',
                '      <saved_value_timestamp/>',
                '      <completion>true</completion>',
                '    </pv>',
            ]
        )
    pv_table.extend(['  </pvlist>', '</pvtable>'])
    (output / "pv_list.pvs").write_text("\n".join(pv_table) + "\n", encoding="utf-8")


def generate(config_dir: Path, output: Path, only: str | None = None) -> list[PVInfo]:
    if only is not None and only not in GENERATABLE_TARGETS:
        raise ValueError(f"Unsupported display generation target: {only}")

    pvs = catalog(config_dir)
    groups = trend_groups(pvs)
    output.mkdir(parents=True, exist_ok=True)
    if only == "overview":
        generate_overview(pvs, output)
        return pvs
    if only in {"psu", "chiller", "environment", "hv", "daq", "global"}:
        generate_subsystem(only, pvs, groups, output)
        return pvs
    if only == "trends":
        generate_trends(groups, output)
        return pvs
    if only == "all-pvs":
        generate_all_pvs(pvs, output)
        write_pv_table(pvs, output)
        return pvs

    generate_overview(pvs, output)
    for subsystem in ("psu", "chiller", "environment", "hv", "daq", "global"):
        generate_subsystem(subsystem, pvs, groups, output)
    generate_trends(groups, output)
    generate_all_pvs(pvs, output)
    write_pv_table(pvs, output)
    return pvs


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="bdx-generate-displays")
    parser.add_argument("--config-dir", default="config/profiles/prototype")
    parser.add_argument("--output-dir", default="phoebus/displays")
    parser.add_argument(
        "--only",
        choices=GENERATABLE_TARGETS,
        help="Generate only one display or display artifact",
    )
    args = parser.parse_args(argv)
    pvs = generate(Path(args.config_dir), Path(args.output_dir), only=args.only)
    if args.only:
        print(f"Generated Phoebus {args.only} display for {len(pvs)} PVs in {args.output_dir}")
    else:
        print(f"Generated Phoebus displays for {len(pvs)} PVs in {args.output_dir}")


if __name__ == "__main__":
    main()
