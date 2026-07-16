import subprocess
from pathlib import Path

from bdx_slow_control import operator_startup


def _terminal_command(tmp_path: Path) -> str:
    root = tmp_path / "bdx-slow-control"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "start_bdx_stack.sh").write_text("", encoding="utf-8")

    return operator_startup._phoebus_terminal_command(
        root,
        "172.22.50.2",
        "overview",
        tmp_path / "phoebus",
    )


def test_operator_startup_never_starts_or_repairs_archiver(tmp_path: Path):
    command = _terminal_command(tmp_path)

    listener_index = command.index("bdx_stack_wait_for_ioc_listener 90")
    ready_pv_index = command.index('bdx_stack_wait_for_pv_read "$IOC_READY_PV" 90')
    report_index = command.index("bdx_stack_report_archiver_status")
    launch_index = command.index("bdx_stack_launch_phoebus")

    assert listener_index < ready_pv_index < report_index < launch_index
    assert "bdx_stack_ensure_archiver" not in command
    assert "bdx_stack_controlled_archiver_registration" not in command
    assert "repair" not in command.lower()


def test_operator_startup_has_no_permissive_live_only_fallback(tmp_path: Path):
    command = _terminal_command(tmp_path)

    assert "bdx_stack_check_archiver_subsystem" not in command
    assert "Continuing without catalog registration" not in command
    assert "live Channel Access where archive data are unavailable" not in command
    assert "--live-only" not in command


def test_operator_startup_archiver_absence_does_not_prevent_phoebus_launch(tmp_path: Path):
    root = tmp_path / "bdx-slow-control"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    trace = tmp_path / "trace.log"
    (scripts / "start_bdx_stack.sh").write_text(
        "\n".join(
            [
                f'TRACE={trace}',
                "IOC_READY_PV=BDX:TEST:READY",
                'record() { printf "%s\\n" "$1" >> "$TRACE"; }',
                "bdx_stack_parse_args() { BDX_STACK_DISPLAY=overview; record parse; }",
                "bdx_stack_load_runtime_environment() { record environment; }",
                "bdx_stack_validate_slow_control_installation() { record validate; }",
                "bdx_stack_print_summary() { record summary; }",
                "bdx_stack_wait_for_ioc_listener() { record listener; }",
                "bdx_stack_wait_for_pv_read() { record ready-pv; }",
                "bdx_stack_report_archiver_status() { record archiver-absent; return 0; }",
                "bdx_stack_launch_phoebus() { record phoebus; }",
            ]
        ),
        encoding="utf-8",
    )

    command = operator_startup._phoebus_terminal_command(
        root,
        "172.22.50.2",
        "overview",
        tmp_path / "phoebus",
    ).replace("exec bash", 'exit "$status"')

    result = subprocess.run(
        ["bash", "-c", command],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert trace.read_text(encoding="utf-8").splitlines() == [
        "parse",
        "environment",
        "validate",
        "summary",
        "listener",
        "ready-pv",
        "archiver-absent",
        "phoebus",
    ]
    assert "Phoebus startup failed" not in result.stderr


def test_operator_startup_terminal_command_is_valid_strict_bash(tmp_path: Path):
    command = _terminal_command(tmp_path)

    result = subprocess.run(
        ["bash", "-n"],
        input=command,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "set -euo pipefail" in command


def test_archiver_only_command_does_not_start_ioc_or_launch_phoebus(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "bdx-slow-control"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "start_bdx_stack.sh").write_text("", encoding="utf-8")
    captured = {}
    monkeypatch.setattr(operator_startup.common, "_repository_root", lambda: root)
    monkeypatch.setattr(
        operator_startup.common, "_read_main_host", lambda _root, _host: "172.22.50.2"
    )
    monkeypatch.setattr(
        operator_startup.subprocess,
        "run",
        lambda command, **kwargs: captured.update(command=command, kwargs=kwargs),
    )

    operator_startup._start_archiver([])

    command = captured["command"][2]
    assert "bdx_stack_start_and_validate_archiver" in command
    assert "bdx_stack_start_ioc_if_needed" not in command
    assert "bdx_stack_launch_phoebus" not in command
    assert "bdx_stack_start_and_validate_archiver 180 true" in command


def test_archiver_no_repair_option_still_validates_components(tmp_path: Path, monkeypatch):
    root = tmp_path / "bdx-slow-control"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "start_bdx_stack.sh").write_text("", encoding="utf-8")
    captured = {}
    monkeypatch.setattr(operator_startup.common, "_repository_root", lambda: root)
    monkeypatch.setattr(
        operator_startup.common, "_read_main_host", lambda _root, _host: "172.22.50.2"
    )
    monkeypatch.setattr(
        operator_startup.subprocess,
        "run",
        lambda command, **kwargs: captured.update(command=command, kwargs=kwargs),
    )

    operator_startup._start_archiver(["--no-repair", "--timeout", "45"])

    command = captured["command"][2]
    assert "bdx_stack_start_and_validate_archiver 45 false" in command
    assert "bdx_stack_start_ioc_if_needed" not in command
    assert "bdx_stack_launch_phoebus" not in command


def _assert_entrypoint_mappings(expected: dict[str, str]) -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")

    for command, target in expected.items():
        assert f'{command} = "bdx_slow_control.{target}"' in text


def test_canonical_lifecycle_entrypoints_are_declared():
    _assert_entrypoint_mappings({
        "bdx_slow_control_start": "operator_startup:slow_control_start_main",
        "bdx_slow_control_kill": "operator_commands:slow_control_kill_main",
        "bdx_slow_control_kill_ioc": "operator_commands:kill_ioc_main",
        "bdx_slow_control_kill_phoebus": "operator_commands:kill_phoebus_main",
        "bdx_archiver_start": "operator_startup:start_archiver_main",
        "bdx_archiver_repair": "operator_startup:repair_archiver_main",
        "bdx_archiver_audit": "operator_startup:audit_archiver_main",
        "bdx_archiver_kill": "operator_commands:archiver_kill_main",
    })


def test_legacy_lifecycle_aliases_remain_declared():
    _assert_entrypoint_mappings({
        "start_slow_control": "operator_startup:slow_control_start_main",
        "kill_slow_control": "operator_commands:slow_control_kill_main",
        "start_archiver": "operator_startup:start_archiver_main",
        "kill_archiver": "operator_commands:archiver_kill_main",
        "bdx_slow_control_start_archiver": "operator_startup:start_archiver_main",
        "bdx_slow_control_repair_archiver": "operator_startup:repair_archiver_main",
        "bdx_slow_control_kill_archiver": "operator_commands:kill_archiver_main",
    })


def test_user_command_installer_lists_canonical_commands_before_legacy_aliases():
    text = Path("scripts/install_user_commands.sh").read_text(encoding="utf-8")
    canonical_start = text.index("canonical_commands=(")
    compatibility_start = text.index("compatibility_aliases=(")
    additional_start = text.index("additional_commands=(")

    assert canonical_start < compatibility_start < additional_start
    canonical_block = text[canonical_start:compatibility_start]
    compatibility_block = text[compatibility_start:additional_start]

    for command in (
        "bdx_slow_control_start",
        "bdx_slow_control_kill",
        "bdx_slow_control_kill_ioc",
        "bdx_slow_control_kill_phoebus",
        "bdx_archiver_start",
        "bdx_archiver_repair",
        "bdx_archiver_audit",
        "bdx_archiver_kill",
    ):
        assert f"    {command}\n" in canonical_block

    for alias in (
        "start_slow_control",
        "kill_slow_control",
        "start_archiver",
        "kill_archiver",
        "bdx_slow_control_start_archiver",
        "bdx_slow_control_repair_archiver",
        "bdx_slow_control_kill_archiver",
    ):
        assert f"    {alias}\n" in compatibility_block
