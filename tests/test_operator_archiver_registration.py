from pathlib import Path

from bdx_slow_control import operator_startup


def test_operator_startup_checks_one_representative_pv_per_subsystem(tmp_path: Path):
    root = tmp_path / "bdx-slow-control"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "start_bdx_stack.sh").write_text("", encoding="utf-8")

    command = operator_startup._archiver_phoebus_terminal_command(
        root,
        "172.22.50.2",
        "overview",
        tmp_path / "phoebus",
    )

    ensure_index = command.index("bdx_stack_ensure_archiver")
    psu_index = command.index('bdx_stack_check_archiver_subsystem "PSU"')
    chiller_index = command.index('bdx_stack_check_archiver_subsystem "Chiller"')
    environment_index = command.index('bdx_stack_check_archiver_subsystem "Environment"')
    launch_index = command.index("bdx_stack_launch_phoebus")

    assert ensure_index < psu_index < chiller_index < environment_index < launch_index
    assert "bdx_stack_controlled_archiver_registration" not in command
    assert "skipping per-PV catalog scan" in command
    assert '"$ARCHIVER_READY_PV"' in command
    assert '"$ARCHIVER_CHILLER_READY_PV"' in command
    assert '"$ARCHIVER_ENV_READY_PV"' in command


def test_operator_startup_registers_only_a_subsystem_with_missing_status(tmp_path: Path):
    root = tmp_path / "bdx-slow-control"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "start_bdx_stack.sh").write_text("", encoding="utf-8")

    command = operator_startup._archiver_phoebus_terminal_command(
        root,
        "172.22.50.2",
        "overview",
        tmp_path / "phoebus",
    )

    assert 'if bdx_stack_archiver_pv_connected "$representative_pv"; then' in command
    assert 'bdx_stack_register_pv_lists "$ARCHIVER_REGISTER_DELAY_SECONDS" "$pv_list"' in command
    assert 'bdx_stack_wait_for_archiver_pv_connection "$representative_pv" 180' in command
