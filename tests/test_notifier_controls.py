import asyncio
import importlib.util
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from bdx_slow_control.iocs.global_system import GlobalIOC, _pv_boolean
from bdx_slow_control.phoebus_generator import generate
from bdx_slow_control.prototype import build_prototype
from bdx_slow_control.runtime import RuntimeSettings


DEFAULT_PROFILE = Path("config/profiles/default")


def test_notifier_control_pvs_are_exposed_by_global_ioc():
    pvdb, _ = build_prototype(DEFAULT_PROFILE)
    required = {
        "BDX:GLOBAL:NOTIFIER_ENABLED",
        "BDX:GLOBAL:NOTIFIER_ONLINE",
        "BDX:GLOBAL:NOTIFIER_STATUS",
        "BDX:GLOBAL:NOTIFIER_HEARTBEAT",
        "BDX:GLOBAL:NOTIFIER_SNOOZE_MINUTES",
        "BDX:GLOBAL:NOTIFIER_SNOOZE_CMD",
        "BDX:GLOBAL:NOTIFIER_RESUME_CMD",
        "BDX:GLOBAL:NOTIFIER_SNOOZE_UNTIL",
        "BDX:GLOBAL:NOTIFIER_LAST_HEARTBEAT",
    }
    assert required.issubset(pvdb)


def test_notifier_boolean_parser_handles_epics_enum_strings():
    assert _pv_boolean("On") is True
    assert _pv_boolean("Off") is False
    assert _pv_boolean(1) is True
    assert _pv_boolean(0) is False


def test_notifier_snooze_and_resume_update_effective_state():
    async def scenario():
        group = GlobalIOC(
            prefix="BDX:GLOBAL:",
            runtime_settings=RuntimeSettings(),
        )
        await group.NOTIFIER_HEARTBEAT.write(value=1)
        await group.NOTIFIER_SNOOZE_MINUTES.write(value=30.0)
        await group.NOTIFIER_SNOOZE_CMD.write(value="On")
        assert _pv_boolean(group.NOTIFIER_ENABLED.value) is False
        assert group.NOTIFIER_STATUS.value == "SNOOZED"
        assert group.NOTIFIER_SNOOZE_UNTIL.value

        await group.NOTIFIER_RESUME_CMD.write(value="On")
        assert _pv_boolean(group.NOTIFIER_ENABLED.value) is True
        assert group.NOTIFIER_STATUS.value == "ACTIVE"
        assert group.NOTIFIER_SNOOZE_UNTIL.value == ""

    asyncio.run(scenario())


def test_overview_contains_notifier_status_and_controls(tmp_path: Path):
    generate(DEFAULT_PROFILE, tmp_path)
    root = ET.parse(tmp_path / "overview.bob").getroot()
    references = {
        element.text
        for element in root.findall(".//pv_name")
        if element.text
    }
    assert {
        "BDX:GLOBAL:NOTIFIER_ONLINE",
        "BDX:GLOBAL:NOTIFIER_STATUS",
        "BDX:GLOBAL:NOTIFIER_SNOOZE_MINUTES",
        "BDX:GLOBAL:NOTIFIER_SNOOZE_CMD",
        "BDX:GLOBAL:NOTIFIER_RESUME_CMD",
        "BDX:GLOBAL:NOTIFIER_SNOOZE_UNTIL",
        "BDX:GLOBAL:NOTIFIER_LAST_HEARTBEAT",
    }.issubset(references)


def test_notifier_wrapper_preserves_core_api_and_persistent_history(monkeypatch, tmp_path: Path):
    notifier_dir = Path("notifier").resolve()
    monkeypatch.syspath_prepend(str(notifier_dir))
    module_path = notifier_dir / "notifier.py"
    spec = importlib.util.spec_from_file_location("bdx_notifier_runtime_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert module.AlarmEngine is module.core.AlarmEngine
    event = module.AlarmEvent(
        rule_id="test-rule",
        label="Test alarm",
        pv="BDX:TEST:PV",
        level="MINOR",
        resolved=False,
        value=1,
        limit="must be zero",
    )
    history = module.EventHistory(tmp_path)
    history.append(event, delivered=False)
    records = history.recent(24.0)
    assert len(records) == 1
    assert records[0]["rule_id"] == "test-rule"
    assert records[0]["delivered"] is False
