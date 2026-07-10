from pathlib import Path

from bdx_slow_control import operator_commands


def test_operator_startup_registers_archiver_catalog_before_phoebus(tmp_path: Path):
    root = tmp_path / "bdx-slow-control"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "start_bdx_stack.sh").write_text("", encoding="utf-8")

    command = operator_commands._archiver_phoebus_terminal_command(
        root,
        "172.22.50.2",
        "overview",
        tmp_path / "phoebus",
    )

    ensure_index = command.index("bdx_stack_ensure_archiver")
    registration_index = command.index("bdx_stack_controlled_archiver_registration")
    launch_index = command.index("bdx_stack_launch_phoebus")

    assert ensure_index < registration_index < launch_index
    assert 'bdx_stack_wait_for_archiver_pv_connection "$ARCHIVER_READY_PV" 180' not in command
