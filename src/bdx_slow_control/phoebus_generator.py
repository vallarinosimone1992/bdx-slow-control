"""Generate Phoebus Display Builder files from the configured PV database."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from .config import DEFAULT_PROFILE_DIR
from .prototype import build_prototype

DEFAULT_TREND_RANGE = "10 minutes"
DEFAULT_TREND_SCAN_PERIOD = 1.0
DEFAULT_TREND_RING_SIZE = 5000
DEFAULT_TREND_ARCHIVE_REQUEST = "RAW"
DEFAULT_ARCHIVER_NAME = "BDX Archiver"
DEFAULT_ARCHIVER_URL = "http://127.0.0.1:17668/retrieval"


def trend_range() -> str:
    """Live trend window, configurable through the BDX_TREND_RANGE environment variable."""
    return os.environ.get("BDX_TREND_RANGE", DEFAULT_TREND_RANGE)


def trend_scan_period() -> float:
    """Live trend scan period used to timestamp Data Browser samples."""
    return float(os.environ.get("BDX_TREND_SCAN_PERIOD", DEFAULT_TREND_SCAN_PERIOD))


def trend_ring_size() -> int:
    """Live Data Browser ring buffer size for each generated trace."""
    ring_size = int(os.environ.get("BDX_TREND_RING_SIZE", DEFAULT_TREND_RING_SIZE))
    if ring_size <= 0:
        raise ValueError("BDX_TREND_RING_SIZE must be a positive integer")
    return ring_size


def trend_archive_request() -> str:
    """Archive retrieval request type used by generated Data Browser traces."""
    request = os.environ.get("BDX_TREND_ARCHIVE_REQUEST", DEFAULT_TREND_ARCHIVE_REQUEST)
    request = request.strip().upper()
    if request not in {"RAW", "OPTIMIZED"}:
        raise ValueError("BDX_TREND_ARCHIVE_REQUEST must be RAW or OPTIMIZED")
    return request


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def archiver_pbraw_url(raw_url: str) -> str | None:
    """Convert an Archiver Appliance retrieval endpoint to Phoebus pbraw syntax."""
    value = raw_url.strip()
    if not value:
        return None
    value = re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^/@]+@([^/]+)", r"\1\2", value)
    if "://" not in value and "@" in value.split("/", 1)[0]:
        value = value.split("@", 1)[1]
    value = value.rstrip("/")
    if value.startswith("pbraw://"):
        return value
    if value.startswith("http://"):
        return f"pbraw://{value.removeprefix('http://')}"
    if value.startswith("https://"):
        return f"pbraw://{value.removeprefix('https://')}"
    if "://" not in value:
        return f"pbraw://{value}"
    return None


@dataclass(frozen=True)
class ArchiveSource:
    name: str
    url: str
    key: int = 1


def archive_source_from_environment() -> ArchiveSource | None:
    """Return the configured Archiver Appliance source, or None for live-only plots."""
    if not env_flag("BDX_ARCHIVER_ENABLED", default=True):
        return None

    raw_url = os.environ.get("BDX_ARCHIVER_URL", DEFAULT_ARCHIVER_URL)
    url = archiver_pbraw_url(raw_url)
    if url is None:
        if env_flag("BDX_ARCHIVER_STRICT_CHECK"):
            raise ValueError(
                "BDX_ARCHIVER_URL must be a valid Archiver Appliance retrieval endpoint"
            )
        return None

    name = os.environ.get("BDX_ARCHIVER_NAME", DEFAULT_ARCHIVER_NAME).strip()
    if not name:
        name = DEFAULT_ARCHIVER_NAME
    return ArchiveSource(name=name, url=url)


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
    axis: int = 0


@dataclass(frozen=True)
class AxisInfo:
    name: str
    right: bool = False


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

    def text_entry(
        self,
        pv: str,
        x: int,
        y: int,
        width: int,
        height: int = 25,
        *,
        precision: int | None = None,
        format_code: int | None = None,
    ) -> ET.Element:
        widget = self.widget("textentry", "2.0.0", "Setpoint", x, y, width, height)
        ET.SubElement(widget, "pv_name").text = pv
        if format_code is not None:
            ET.SubElement(widget, "format").text = str(format_code)
        if precision is not None:
            ET.SubElement(widget, "precision").text = str(precision)
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
        value = action_value_for_pv(pv, value)
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
        ET.SubElement(action, "target").text = "tab"
        ET.SubElement(action, "description").text = f"Open {text}"
        ET.SubElement(widget, "text").text = text
        ET.SubElement(widget, "tooltip").text = "$(actions)"
        return widget

    def open_file_button(
        self,
        text: str,
        filename: str,
        x: int,
        y: int,
        width: int,
        height: int = 30,
    ) -> ET.Element:
        widget = self.widget("action_button", "3.0.0", "OpenFile", x, y, width, height)
        actions = ET.SubElement(widget, "actions")
        action = ET.SubElement(actions, "action", {"type": "open_file"})
        ET.SubElement(action, "file").text = filename
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


def action_value_for_pv(pv: str, value: str) -> str:
    """Return a display-safe action value for known boolean command PVs."""
    if value == "1" and pv.endswith(
        (
            ":ALLOFF_CMD",
            ":APPLY_CMD",
            ":APPLY_SETPOINT_CMD",
            ":CLEAR_ERROR_CMD",
            ":INTERLOCK_RESET_CMD",
            ":INTERLOCK_TEST_CMD",
            ":OUTPUT_SET",
            ":RUN_SET",
        )
    ):
        return "On"
    if value == "0" and pv.endswith((":OUTPUT_SET", ":RUN_SET")):
        return "Off"
    return value


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
                if pv.name.endswith(":OUTPUT_SET"):
                    display.action_button(
                        "ON",
                        pv.name,
                        "1",
                        870,
                        y,
                        64,
                        25,
                        confirm=f"Switch output on for {pv.name}?",
                        background=(170, 225, 180),
                    )
                    display.action_button(
                        "OFF",
                        pv.name,
                        "0",
                        941,
                        y,
                        64,
                        25,
                        confirm=f"Switch output off for {pv.name}?",
                        background=(255, 190, 190),
                    )
                elif pv.name.endswith(":RUN_SET"):
                    display.action_button(
                        "START",
                        pv.name,
                        "1",
                        870,
                        y,
                        64,
                        25,
                        confirm=f"Start device through {pv.name}?",
                        background=(170, 225, 180),
                    )
                    display.action_button(
                        "STOP",
                        pv.name,
                        "0",
                        941,
                        y,
                        64,
                        25,
                        confirm=f"Stop device through {pv.name}?",
                        background=(255, 190, 190),
                    )
                else:
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
    axes: tuple[AxisInfo, ...] | None = None

    def resolved_axes(self) -> tuple[AxisInfo, ...]:
        return self.axes or (AxisInfo(self.y_axis_title),)


@dataclass(frozen=True)
class ProfileDisplayOptions:
    chiller_pressure_enabled: bool = False
    chiller_external_temperature_enabled: bool = False


def display_options(config_dir: Path) -> ProfileDisplayOptions:
    chiller_config = config_dir / "chiller.json"
    if not chiller_config.exists():
        return ProfileDisplayOptions()
    with chiller_config.open(encoding="utf-8") as stream:
        raw = json.load(stream)
    device = raw.get("device", {})
    if not isinstance(device, dict):
        return ProfileDisplayOptions()
    return ProfileDisplayOptions(
        chiller_pressure_enabled=bool(device.get("pressure_enabled", False)),
        chiller_external_temperature_enabled=bool(
            device.get("external_temperature_enabled", False)
        ),
    )


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


def _psu_devices_from_names(names: set[str]) -> list[str]:
    devices = {
        parts[2]
        for name in names
        if name.startswith("BDX:PSU:")
        for parts in [name.split(":")]
        if len(parts) >= 4
    }
    return sorted(devices)


def _psu_channels_from_names(names: set[str], device: str) -> list[str]:
    prefix = f"BDX:PSU:{device}:"
    channels = {
        parts[3]
        for name in names
        if name.startswith(prefix)
        for parts in [name.split(":")]
        if len(parts) >= 5 and parts[3].startswith("CH")
    }
    return sorted(channels)


def trend_groups(
    pvs: Sequence[PVInfo],
    options: ProfileDisplayOptions | None = None,
) -> dict[str, list[TrendGroup]]:
    options = options or ProfileDisplayOptions()
    names = {pv.name for pv in pvs}
    groups: dict[str, list[TrendGroup]] = {
        "psu": [],
        "chiller": [],
        "environment": [],
        "hv": [],
        "daq": [],
        "global": [],
    }

    for device in _psu_devices_from_names(names):
        traces: list[TraceInfo] = []
        for channel in _psu_channels_from_names(names, device):
            voltage_pv = f"BDX:PSU:{device}:{channel}:VOLTAGE_RBV"
            current_pv = f"BDX:PSU:{device}:{channel}:CURRENT_RBV"
            if voltage_pv in names:
                traces.append(TraceInfo(voltage_pv, f"{device} {channel} voltage", axis=0))
            if current_pv in names:
                traces.append(TraceInfo(current_pv, f"{device} {channel} current", axis=1))
        if traces:
            groups["psu"].append(
                TrendGroup(
                    f"{device} actual voltage and current",
                    traces,
                    "Voltage [V]",
                    axes=(AxisInfo("Voltage [V]"), AxisInfo("Current [A]", right=True)),
                )
            )

    for subsystem in ("HV",):
        prefix = f"BDX:{subsystem}:"
        voltage = sorted(
            name
            for name in names
            if name.startswith(prefix)
            and name.endswith(("VOLTAGE_RBV", "OVP_RBV"))
        )
        current = sorted(
            name for name in names if name.startswith(prefix) and name.endswith(("CURRENT_RBV", "CURRENT_LIMIT_RBV", "OCP_RBV"))
        )
        key = subsystem.lower()
        if voltage:
            groups[key].append(TrendGroup(f"{subsystem} voltage and protection", traces_for_pvs(voltage)))
        if current:
            groups[key].append(TrendGroup(f"{subsystem} current and protection", traces_for_pvs(current)))

    chiller_temp = sorted(
        name
        for name in names
        if name.startswith("BDX:CHILLER:")
        and name.rsplit(":", 1)[-1]
        in {"CONTROLLED_TEMPERATURE_RBV", "BATH_TEMPERATURE_RBV", "SETPOINT_RBV"}
    )
    chiller_pressure = sorted(
        name for name in names if name.startswith("BDX:CHILLER:") and name.endswith("PRESSURE_RBV")
    )
    chiller_external = sorted(
        name
        for name in names
        if name.startswith("BDX:CHILLER:") and name.endswith("EXTERNAL_TEMPERATURE_RBV")
    )
    if chiller_temp:
        groups["chiller"].append(
            TrendGroup("Chiller temperature", traces_for_pvs(chiller_temp), "Temperature [degC]")
        )
    if options.chiller_pressure_enabled and chiller_pressure:
        groups["chiller"].append(TrendGroup("Chiller pressure", traces_for_pvs(chiller_pressure)))
    if options.chiller_external_temperature_enabled and chiller_external:
        groups["chiller"].append(
            TrendGroup(
                "Chiller external temperature",
                traces_for_pvs(chiller_external),
                "Temperature [degC]",
            )
        )

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
    for period in (1, 2, 5, 10):
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
    display.label("Allowed period: 1–3600 s.", 995, y, 240, 28)
    return y + 42


def generate_overview(
    pvs: Sequence[PVInfo],
    output: Path,
    options: ProfileDisplayOptions | None = None,
) -> None:
    options = options or ProfileDisplayOptions()
    _remove_subsystem_plot_files(output, "overview")
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
            environment_group.resolved_axes(),
        )
        display.databrowser(plt_filename, 20, y, 670, 360)

    names = {pv.name for pv in pvs}
    chiller_traces = [
        name
        for name in (
            "BDX:CHILLER:CHILLER1:CONTROLLED_TEMPERATURE_RBV",
            "BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV",
            "BDX:CHILLER:CHILLER1:SETPOINT_RBV",
        )
        if name in names
    ]
    if options.chiller_pressure_enabled and "BDX:CHILLER:CHILLER1:PRESSURE_RBV" in names:
        chiller_traces.append("BDX:CHILLER:CHILLER1:PRESSURE_RBV")
    if (
        options.chiller_external_temperature_enabled
        and "BDX:CHILLER:CHILLER1:EXTERNAL_TEMPERATURE_RBV" in names
    ):
        chiller_traces.append("BDX:CHILLER:CHILLER1:EXTERNAL_TEMPERATURE_RBV")
    if chiller_traces:
        write_databrowser_plt(
            output / "overview_chiller.plt",
            "Chiller",
            traces_for_pvs(chiller_traces),
            "Value",
        )
        display.databrowser("overview_chiller.plt", 710, y, 670, 360)
    display.write(output / "overview.bob")


def _subsystem_pv_names(pvs: Sequence[PVInfo]) -> set[str]:
    return {pv.name for pv in pvs}


def _psu_layout(pvs: Sequence[PVInfo]) -> dict[str, list[str]]:
    names = _subsystem_pv_names(pvs)
    return {
        device: _psu_channels_from_names(names, device)
        for device in _psu_devices_from_names(names)
    }


def _pv(prefix: str, suffix: str) -> str:
    return f"{prefix}{suffix}"


def _remove_subsystem_plot_files(output: Path, subsystem: str) -> None:
    for path in output.glob(f"{subsystem}_*.plt"):
        path.unlink()


def _add_readback_pair(
    display: Display,
    label: str,
    pv: str,
    x: int,
    y: int,
    *,
    width: int = 112,
    unit: str = "",
    size: float = 24.0,
) -> None:
    display.label(label, x, y, width + 25, 20, size=11, bold=True)
    display.text_update(
        pv,
        x,
        y + 22,
        width,
        40,
        size=size,
        bold=True,
        precision=2,
        format_code=1,
        show_units=False,
        background=(238, 244, 248),
    )
    if unit:
        display.label(unit, x + width + 6, y + 30, 34, 24, size=13, bold=True)


def _add_psu_channel_card(
    display: Display,
    device: str,
    channel: str,
    x: int,
    y: int,
) -> None:
    prefix = f"BDX:PSU:{device}:{channel}:"
    display.label("", x, y, 650, 218, background=(245, 248, 250))
    display.label(f"{device} {channel}", x + 14, y + 10, 130, 28, size=18, bold=True)

    _add_readback_pair(display, "Actual voltage", _pv(prefix, "VOLTAGE_RBV"), x + 14, y + 48, unit="V")
    _add_readback_pair(display, "Actual current", _pv(prefix, "CURRENT_RBV"), x + 174, y + 48, unit="A")
    _add_readback_pair(
        display,
        "Applied voltage",
        _pv(prefix, "VOLTAGE_SET_RBV"),
        x + 334,
        y + 48,
        width=96,
        unit="V",
        size=16,
    )
    _add_readback_pair(
        display,
        "Current limit",
        _pv(prefix, "CURRENT_LIMIT_RBV"),
        x + 470,
        y + 48,
        width=96,
        unit="A",
        size=16,
    )

    display.label("Requested voltage", x + 14, y + 126, 130, 20, size=11, bold=True)
    display.text_entry(
        _pv(prefix, "VOLTAGE_REQUEST"),
        x + 14,
        y + 148,
        108,
        28,
        precision=3,
        format_code=1,
    )
    display.label("V", x + 127, y + 151, 22, 24, size=12, bold=True)
    display.label("Requested current", x + 158, y + 126, 130, 20, size=11, bold=True)
    display.text_entry(
        _pv(prefix, "CURRENT_LIMIT_REQUEST"),
        x + 158,
        y + 148,
        108,
        28,
        precision=3,
        format_code=1,
    )
    display.label("A", x + 271, y + 151, 22, 24, size=12, bold=True)
    display.action_button(
        "APPLY",
        _pv(prefix, "APPLY_CMD"),
        "1",
        x + 304,
        y + 146,
        88,
        32,
        background=(190, 215, 245),
    )
    display.text_update(_pv(prefix, "APPLY_STATUS"), x + 406, y + 146, 96, 28, size=12)
    display.text_update(_pv(prefix, "APPLY_MESSAGE"), x + 506, y + 146, 128, 28, size=10)

    display.label("Output", x + 14, y + 186, 58, 22, size=11, bold=True)
    display.led(_pv(prefix, "OUTPUT_RBV"), x + 76, y + 184, 26, 26)
    display.text_update(_pv(prefix, "OUTPUT_STATE"), x + 108, y + 184, 72, 26, size=12, bold=True)
    display.action_button(
        "ON",
        _pv(prefix, "OUTPUT_SET"),
        "1",
        x + 192,
        y + 184,
        64,
        28,
        confirm=f"Switch {device} {channel} output ON?",
        background=(170, 225, 180),
    )
    display.action_button(
        "OFF",
        _pv(prefix, "OUTPUT_SET"),
        "0",
        x + 262,
        y + 184,
        64,
        28,
        confirm=f"Switch {device} {channel} output OFF?",
        background=(255, 190, 190),
    )
    display.label("Comm", x + 348, y + 188, 46, 20, size=11, bold=True)
    display.led(_pv(prefix, "COMM_OK"), x + 397, y + 184, 26, 26)
    display.text_update(_pv(prefix, "LAST_UPDATE"), x + 430, y + 184, 204, 26, size=10)
    display.text_update(_pv(prefix, "ERROR_MESSAGE"), x + 148, y + 12, 486, 24, size=10)


def generate_psu_operator(
    pvs: Sequence[PVInfo],
    chart_groups: Sequence[TrendGroup],
    output: Path,
) -> None:
    _remove_subsystem_plot_files(output, "psu")
    layout = _psu_layout(pvs)
    rows = sum((len(channels) + 1) // 2 for channels in layout.values())
    plot_rows = (len(chart_groups) + 1) // 2
    height = max(960, 205 + rows * 245 + plot_rows * 360)
    display = Display("BDX PSU", 1400, height)
    add_header(display, "BDX low-voltage power supplies")
    display.open_button("Expert", "psu_expert.bob", 1220, 12, 140, 32)

    y = 105
    for index, device in enumerate(layout):
        x = 20 + index * 690
        prefix = f"BDX:PSU:{device}:"
        display.label("", x, y, 670, 78, background=(238, 243, 247))
        display.label(device, x + 12, y + 10, 70, 28, size=18, bold=True)
        display.label("Comm", x + 100, y + 12, 50, 22, size=11, bold=True)
        display.led(_pv(prefix, "COMM_OK"), x + 154, y + 9, 28, 28)
        display.text_update(_pv(prefix, "COMM_STATUS"), x + 192, y + 9, 120, 28, size=12)
        display.label("Last update", x + 324, y + 12, 92, 22, size=11, bold=True)
        display.text_update(_pv(prefix, "LAST_UPDATE"), x + 420, y + 9, 150, 28, size=10)
        display.action_button(
            "ALL OFF",
            _pv(prefix, "ALLOFF_CMD"),
            "1",
            x + 575,
            y + 8,
            80,
            30,
            confirm=f"Switch all outputs OFF for {device}?",
            background=(255, 170, 170),
        )
        display.text_update(_pv(prefix, "ERROR_MESSAGE"), x + 12, y + 44, 642, 24, size=10)

    y += 100
    for device, channels in layout.items():
        display.label(device, 20, y, 130, 30, size=20, bold=True)
        y += 40
        for index, channel in enumerate(channels):
            column = index % 2
            row = index // 2
            _add_psu_channel_card(
                display,
                device,
                channel,
                20 + column * 690,
                y + row * 240,
            )
        y += ((len(channels) + 1) // 2) * 240 + 20

    for index, group in enumerate(chart_groups):
        column = index % 2
        row = index // 2
        plt_filename = f"psu_{index}_{slug(group.title)}.plt"
        write_databrowser_plt(
            output / plt_filename,
            group.title,
            group.traces,
            group.y_axis_title,
            group.resolved_axes(),
        )
        x = 20 + column * 690
        plot_y = y + row * 360
        display.label(group.title, x, plot_y, 420, 28, size=15, bold=True)
        display.open_file_button("Full history", plt_filename, x + 520, plot_y, 150, 30)
        display.databrowser(plt_filename, x, plot_y + 38, 670, 310)

    display.write(output / "psu.bob")
    generate_psu_expert(pvs, output)


def generate_psu_expert(pvs: Sequence[PVInfo], output: Path) -> None:
    height = 180 + len(pvs) * 29
    display = Display("BDX PSU EXPERT", 1400, max(760, height))
    add_header(display, "BDX PSU EXPERT")
    display.open_button("Back to PSU", "psu.bob", 20, 105, 180, 32)
    add_pv_table(display, pvs, 150)
    display.write(output / "psu_expert.bob")


def _chiller_prefix(pvs: Sequence[PVInfo]) -> str | None:
    for pv in pvs:
        if pv.name.startswith("BDX:CHILLER:") and pv.name.endswith(":SETPOINT_RBV"):
            return pv.name.removesuffix("SETPOINT_RBV")
    return None


def generate_chiller_operator(
    pvs: Sequence[PVInfo],
    chart_groups: Sequence[TrendGroup],
    output: Path,
) -> None:
    _remove_subsystem_plot_files(output, "chiller")
    prefix = _chiller_prefix(pvs)
    if prefix is None:
        return
    display = Display("BDX Chiller", 1400, max(980, 520 + len(chart_groups) * 360))
    add_header(display, "BDX chiller")
    display.open_button("Expert", "chiller_expert.bob", 1220, 12, 140, 32)

    y = 105
    display.label("", 20, y, 1360, 78, background=(238, 243, 247))
    display.label("Communication", 36, y + 10, 120, 22, size=11, bold=True)
    display.led(_pv(prefix, "COMM_OK"), 160, y + 8, 28, 28)
    display.text_update(_pv(prefix, "COMM_STATUS"), 198, y + 8, 125, 28, size=12)
    display.label("Run state", 350, y + 10, 76, 22, size=11, bold=True)
    display.led(_pv(prefix, "RUN_RBV"), 430, y + 8, 28, 28)
    display.text_update(_pv(prefix, "RUN_STATE"), 468, y + 8, 120, 28, size=12, bold=True)
    display.label("Fault", 620, y + 10, 48, 22, size=11, bold=True)
    display.led(_pv(prefix, "FAULT"), 672, y + 8, 28, 28, off_color=(40, 170, 80), on_color=(190, 40, 40))
    display.label("Last update", 735, y + 10, 92, 22, size=11, bold=True)
    display.text_update(_pv(prefix, "LAST_UPDATE"), 830, y + 8, 230, 28, size=11)
    display.text_update(_pv(prefix, "ERROR_MESSAGE"), 36, y + 44, 1300, 24, size=10)

    y += 105
    display.label("", 20, y, 430, 240, background=(245, 248, 250))
    display.label("Temperatures", 36, y + 12, 180, 28, size=18, bold=True)
    _add_readback_pair(
        display,
        "Controlled",
        _pv(prefix, "CONTROLLED_TEMPERATURE_RBV"),
        36,
        y + 58,
        width=126,
        unit="degC",
        size=24,
    )
    _add_readback_pair(
        display,
        "Bath",
        _pv(prefix, "BATH_TEMPERATURE_RBV"),
        220,
        y + 58,
        width=126,
        unit="degC",
        size=24,
    )
    _add_readback_pair(
        display,
        "Applied setpoint",
        _pv(prefix, "SETPOINT_RBV"),
        36,
        y + 148,
        width=126,
        unit="degC",
        size=22,
    )

    display.label("", 470, y, 430, 240, background=(245, 248, 250))
    display.label("Setpoint request", 486, y + 12, 220, 28, size=18, bold=True)
    display.label("Requested setpoint", 486, y + 58, 150, 22, size=11, bold=True)
    display.text_entry(
        _pv(prefix, "SETPOINT_REQUEST"),
        486,
        y + 84,
        126,
        30,
        precision=2,
        format_code=1,
    )
    display.label("degC", 620, y + 88, 45, 24, size=12, bold=True)
    display.action_button(
        "APPLY",
        _pv(prefix, "APPLY_SETPOINT_CMD"),
        "1",
        680,
        y + 82,
        90,
        32,
        background=(190, 215, 245),
    )
    display.text_update(_pv(prefix, "APPLY_STATUS"), 486, y + 130, 120, 26, size=12)
    display.text_update(_pv(prefix, "APPLY_MESSAGE"), 616, y + 130, 250, 26, size=10)
    display.label("Allowed setpoint: 5 to 40 degC", 486, y + 174, 260, 24, size=11)

    display.label("", 920, y, 460, 240, background=(245, 248, 250))
    display.label("Operation", 936, y + 12, 160, 28, size=18, bold=True)
    display.action_button(
        "START",
        _pv(prefix, "RUN_SET"),
        "1",
        936,
        y + 58,
        100,
        34,
        confirm="Start the chiller?",
        background=(170, 225, 180),
    )
    display.action_button(
        "STOP",
        _pv(prefix, "RUN_SET"),
        "0",
        1050,
        y + 58,
        100,
        34,
        confirm="Stop the chiller and place it in standby?",
        background=(255, 190, 190),
    )
    display.label("Pump stage", 936, y + 112, 90, 22, size=11, bold=True)
    display.text_update(_pv(prefix, "PUMP_STAGE"), 1030, y + 108, 110, 26, size=12)
    display.label("Cooling mode", 936, y + 146, 100, 22, size=11, bold=True)
    display.text_update(_pv(prefix, "COOLING_MODE"), 1042, y + 142, 110, 26, size=12)
    display.label("Deviation", 936, y + 180, 80, 22, size=11, bold=True)
    display.text_update(_pv(prefix, "TEMPERATURE_DEVIATION_RBV"), 1020, y + 176, 86, 26, precision=2, format_code=1)
    display.text_update(_pv(prefix, "DEVIATION_STATUS"), 1116, y + 176, 120, 26, size=12, bold=True)

    y += 270
    for index, group in enumerate(chart_groups):
        plt_filename = f"chiller_{index}_{slug(group.title)}.plt"
        write_databrowser_plt(
            output / plt_filename,
            group.title,
            group.traces,
            group.y_axis_title,
            group.resolved_axes(),
        )
        plot_y = y + index * 360
        display.label(group.title, 20, plot_y, 420, 28, size=15, bold=True)
        display.open_file_button("Full history", plt_filename, 1230, plot_y, 150, 30)
        display.databrowser(plt_filename, 20, plot_y + 38, 1360, 310)

    display.write(output / "chiller.bob")
    generate_chiller_expert(pvs, output)


def generate_chiller_expert(pvs: Sequence[PVInfo], output: Path) -> None:
    height = 180 + len(pvs) * 29
    display = Display("BDX CHILLER EXPERT", 1400, max(760, height))
    add_header(display, "BDX CHILLER EXPERT")
    display.open_button("Back to chiller", "chiller.bob", 20, 105, 200, 32)
    add_pv_table(display, pvs, 150)
    display.write(output / "chiller_expert.bob")


def generate_subsystem(
    subsystem: str,
    pvs: Sequence[PVInfo],
    groups: dict[str, list[TrendGroup]],
    output: Path,
) -> None:
    selected = [pv for pv in pvs if pv.subsystem == subsystem]
    chart_groups = groups.get(subsystem, [])
    if subsystem == "psu":
        generate_psu_operator(selected, chart_groups, output)
        return
    if subsystem == "chiller":
        generate_chiller_operator(selected, chart_groups, output)
        return

    summary_traces = temperature_traces(chart_groups) if subsystem == "environment" else []
    health_height = 52 if subsystem == "environment" and any(
        pv.name in {"BDX:ENV:HEARTBEAT", "BDX:ENV:LAST_TEMPERATURE_UPDATE"}
        for pv in selected
    ) else 0
    summary_height = temperature_summary_height(len(summary_traces))
    chart_rows = (len(chart_groups) + 1) // 2
    plot_height = chart_rows * 360
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
            write_databrowser_plt(
                output / plt_filename,
                group.title,
                group.traces,
                group.y_axis_title,
                group.resolved_axes(),
            )
            x = 20 + column * 690
            plot_y = y + row * 360
            display.label(group.title, x, plot_y, 420, 28, size=15, bold=True)
            display.open_file_button("Full history", plt_filename, x + 520, plot_y, 150, 30)
            display.databrowser(
                plt_filename,
                x,
                plot_y + 38,
                670,
                310,
            )
        y += chart_rows * 360

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
    _remove_subsystem_plot_files(output, "trends")
    all_groups: list[TrendGroup] = []
    for subsystem in ("environment", "chiller", "psu", "hv", "global"):
        all_groups.extend(groups.get(subsystem, []))
    rows = (len(all_groups) + 1) // 2
    display = Display("BDX Trends", 1400, 170 + rows * 360)
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
        write_databrowser_plt(
            output / plt_filename,
            group.title,
            group.traces,
            group.y_axis_title,
            group.resolved_axes(),
        )
        x = 20 + column * 690
        plot_y = y + row * 360
        display.label(group.title, x, plot_y, 420, 28, size=15, bold=True)
        display.open_file_button("Full history", plt_filename, x + 520, plot_y, 150, 30)
        display.databrowser(
            plt_filename,
            x,
            plot_y + 38,
            670,
            310,
        )
    display.write(output / "trends.bob")


def generate_all_pvs(pvs: Sequence[PVInfo], output: Path) -> None:
    height = 145 + len(pvs) * 29
    display = Display("BDX All PVs", 1200, height)
    add_header(display, "BDX complete PV table")
    add_pv_table(display, pvs, 105)
    display.write(output / "all_pvs.bob")


def write_databrowser_plt(
    path: Path,
    title: str,
    traces: Sequence[TraceInfo],
    y_axis_title: str,
    axes: Sequence[AxisInfo] | None = None,
) -> None:
    """Write a Data Browser *.plt file backing an embedded 'databrowser' widget.

    Traces use live Channel Access and, by default, the configured BDX Archiver
    Appliance pbraw data source. Set BDX_ARCHIVER_ENABLED=false for live-only
    plots.
    """
    resolved_axes = tuple(axes or (AxisInfo(y_axis_title),))
    archive_source = archive_source_from_environment()
    ring_size = trend_ring_size()
    request = trend_archive_request()
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<databrowser>',
        f'    <title>{escape(title)}</title>',
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
    ]
    for axis in resolved_axes:
        lines.extend(
            [
                '        <axis>',
                '            <visible>true</visible>',
                f'            <name>{escape(axis.name)}</name>',
                '            <use_axis_name>true</use_axis_name>',
                '            <use_trace_names>false</use_trace_names>',
                f'            <right>{str(axis.right).lower()}</right>',
                '            <min>0.0</min>',
                '            <max>100.0</max>',
                '            <grid>true</grid>',
                '            <autoscale>true</autoscale>',
                '            <log_scale>false</log_scale>',
                '        </axis>',
            ]
        )
    lines.extend(
        [
            '    </axes>',
        '    <annotations>',
        '    </annotations>',
        '    <pvlist>',
        ]
    )
    for index, trace_info in enumerate(traces):
        rgb = PALETTE[index % len(PALETTE)]
        lines.extend(
            [
                '        <pv>',
                f'            <display_name>{escape(trace_info.label)}</display_name>',
                '            <visible>true</visible>',
                f'            <name>{escape(trace_info.pv)}</name>',
                f'            <axis>{trace_info.axis}</axis>',
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
                f'            <ring_size>{ring_size}</ring_size>',
                f'            <request>{request}</request>',
            ]
        )
        if archive_source is not None:
            lines.extend(
                [
                    '            <archive>',
                    f'                <name>{escape(archive_source.name)}</name>',
                    f'                <url>{escape(archive_source.url)}</url>',
                    f'                <key>{archive_source.key}</key>',
                    '            </archive>',
                ]
            )
        lines.append('        </pv>')
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
    options = display_options(config_dir)
    groups = trend_groups(pvs, options)
    output.mkdir(parents=True, exist_ok=True)
    if only == "overview":
        generate_overview(pvs, output, options)
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

    generate_overview(pvs, output, options)
    for subsystem in ("psu", "chiller", "environment", "hv", "daq", "global"):
        generate_subsystem(subsystem, pvs, groups, output)
    generate_trends(groups, output)
    generate_all_pvs(pvs, output)
    write_pv_table(pvs, output)
    return pvs


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="bdx-generate-displays")
    parser.add_argument("--config-dir", default=str(DEFAULT_PROFILE_DIR))
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
