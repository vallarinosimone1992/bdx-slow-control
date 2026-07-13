import subprocess
from pathlib import Path

from bdx_slow_control import operator_startup


def _terminal_command(tmp_path: Path) -> str:
    root = tmp_path / "bdx-slow-control"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "start_bdx_stack.sh").write_text("", encoding="utf-8")

    return operator_startup._archiver_phoebus_terminal_command(
        root,
        "172.22.50.2",
        "overview",
        tmp_path / "phoebus",
    )


def test_operator_startup_registers_archiver_catalog_before_phoebus(tmp_path: Path):
    command = _terminal_command(tmp_path)

    listener_index = command.index("bdx_stack_wait_for_ioc_listener 90")
    ready_pv_index = command.index('bdx_stack_wait_for_pv_read "$IOC_READY_PV" 90')
    ensure_index = command.index("bdx_stack_ensure_archiver")
    registration_index = command.index("bdx_stack_controlled_archiver_registration")
    launch_index = command.index("bdx_stack_launch_phoebus")

    assert (
        listener_index
        < ready_pv_index
        < ensure_index
        < registration_index
        < launch_index
    )
    assert "BDX_ARCHIVER_STRICT_CHECK=true" in command


def test_operator_startup_has_no_permissive_live_only_fallback(tmp_path: Path):
    command = _terminal_command(tmp_path)

    assert "bdx_stack_check_archiver_subsystem" not in command
    assert "Continuing without catalog registration" not in command
    assert "live Channel Access where archive data are unavailable" not in command
    assert "BDX_ARCHIVER_STRICT_CHECK=false" not in command


def test_operator_startup_registration_failure_prevents_phoebus_launch(tmp_path: Path):
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
                "bdx_stack_validate_installation() { record validate; }",
                "bdx_stack_print_summary() { record summary; }",
                "bdx_stack_wait_for_ioc_listener() { record listener; }",
                "bdx_stack_wait_for_pv_read() { record ready-pv; }",
                "bdx_stack_ensure_archiver() { record ensure; }",
                "bdx_stack_controlled_archiver_registration() { record registration; return 23; }",
                "bdx_stack_launch_phoebus() { record phoebus; }",
            ]
        ),
        encoding="utf-8",
    )

    command = operator_startup._archiver_phoebus_terminal_command(
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

    assert result.returncode == 23
    assert trace.read_text(encoding="utf-8").splitlines() == [
        "parse",
        "environment",
        "validate",
        "summary",
        "listener",
        "ready-pv",
        "ensure",
        "registration",
    ]
    assert "Phoebus launch is blocked" in result.stderr


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
