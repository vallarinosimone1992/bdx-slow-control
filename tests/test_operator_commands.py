from pathlib import Path
import subprocess

import pytest

from bdx_slow_control import operator_commands


def _prepare_root(tmp_path: Path) -> Path:
    root = tmp_path / "bdx-slow-control"
    (root / ".venv/bin").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / ".venv/bin/bdx-prototype-ioc").write_text("", encoding="utf-8")
    (root / "scripts/start_bdx_stack.sh").write_text("", encoding="utf-8")
    return root


def test_raspberry_ioc_responding_uses_dedicated_ca_address(monkeypatch, tmp_path: Path):
    caproto_get = tmp_path / "caproto-get"
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert operator_commands.raspberry_ioc_responding(caproto_get) is True
    assert captured["command"][-1] == operator_commands.RASPBERRY_READY_PV
    assert captured["env"]["EPICS_CA_ADDR_LIST"] == operator_commands.RASPBERRY_HOST
    assert captured["env"]["EPICS_CA_AUTO_ADDR_LIST"] == "NO"


def test_start_requires_graphical_desktop(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    with pytest.raises(operator_commands.OperatorCommandError, match="graphical desktop"):
        operator_commands._start_slow_control([])


def test_start_opens_ioc_and_archiver_phoebus_terminals(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    root = _prepare_root(tmp_path)
    opened = []

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(operator_commands, "_repository_root", lambda: root)
    monkeypatch.setattr(
        operator_commands,
        "_read_main_host",
        lambda root, explicit: "172.22.50.2",
    )
    monkeypatch.setattr(
        operator_commands,
        "_caproto_get",
        lambda root: root / ".venv/bin/caproto-get",
    )
    monkeypatch.setattr(
        operator_commands,
        "_resolve_phoebus_home",
        lambda explicit: tmp_path / "phoebus",
    )
    monkeypatch.setattr(
        operator_commands,
        "_terminal_program",
        lambda: ("/usr/bin/gnome-terminal", "gnome"),
    )
    monkeypatch.setattr(
        operator_commands,
        "raspberry_ioc_responding",
        lambda command: False,
    )
    monkeypatch.setattr(operator_commands, "_port_is_listening", lambda host, port: False)
    monkeypatch.setattr(
        operator_commands,
        "_recorded_process_running",
        lambda pid_file, markers: False,
    )
    monkeypatch.setattr(
        operator_commands,
        "_open_terminal",
        lambda title, command: opened.append((title, command)),
    )

    operator_commands._start_slow_control([])

    output = capsys.readouterr()
    assert "start-bdx-raspberry-ioc" in output.err
    assert [title for title, _command in opened] == [
        "BDX Main IOC",
        "BDX Archiver and Phoebus",
    ]
    assert "bdx-prototype-ioc" in opened[0][1]
    assert "bdx_stack_ensure_archiver" in opened[1][1]
    assert "bdx_stack_launch_phoebus" in opened[1][1]


def test_start_does_not_duplicate_running_ioc(monkeypatch, tmp_path: Path):
    root = _prepare_root(tmp_path)
    opened = []

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(operator_commands, "_repository_root", lambda: root)
    monkeypatch.setattr(
        operator_commands,
        "_read_main_host",
        lambda root, explicit: "172.22.50.2",
    )
    monkeypatch.setattr(
        operator_commands,
        "_caproto_get",
        lambda root: root / ".venv/bin/caproto-get",
    )
    monkeypatch.setattr(
        operator_commands,
        "_resolve_phoebus_home",
        lambda explicit: tmp_path / "phoebus",
    )
    monkeypatch.setattr(
        operator_commands,
        "_terminal_program",
        lambda: ("/usr/bin/gnome-terminal", "gnome"),
    )
    monkeypatch.setattr(
        operator_commands,
        "raspberry_ioc_responding",
        lambda command: True,
    )
    monkeypatch.setattr(operator_commands, "_port_is_listening", lambda host, port: True)
    monkeypatch.setattr(
        operator_commands,
        "_recorded_process_running",
        lambda pid_file, markers: False,
    )
    monkeypatch.setattr(
        operator_commands,
        "_open_terminal",
        lambda title, command: opened.append((title, command)),
    )

    operator_commands._start_slow_control([])

    assert [title for title, _command in opened] == ["BDX Archiver and Phoebus"]


def test_start_does_not_duplicate_running_phoebus(monkeypatch, tmp_path: Path):
    root = _prepare_root(tmp_path)
    opened = []

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(operator_commands, "_repository_root", lambda: root)
    monkeypatch.setattr(
        operator_commands,
        "_read_main_host",
        lambda root, explicit: "172.22.50.2",
    )
    monkeypatch.setattr(
        operator_commands,
        "_caproto_get",
        lambda root: root / ".venv/bin/caproto-get",
    )
    monkeypatch.setattr(
        operator_commands,
        "_resolve_phoebus_home",
        lambda explicit: tmp_path / "phoebus",
    )
    monkeypatch.setattr(
        operator_commands,
        "_terminal_program",
        lambda: ("/usr/bin/gnome-terminal", "gnome"),
    )
    monkeypatch.setattr(
        operator_commands,
        "raspberry_ioc_responding",
        lambda command: True,
    )
    monkeypatch.setattr(operator_commands, "_port_is_listening", lambda host, port: False)
    monkeypatch.setattr(
        operator_commands,
        "_recorded_process_running",
        lambda pid_file, markers: True,
    )
    monkeypatch.setattr(
        operator_commands,
        "_open_terminal",
        lambda title, command: opened.append((title, command)),
    )

    operator_commands._start_slow_control([])

    assert [title for title, _command in opened] == ["BDX Main IOC"]


def test_ioc_terminal_records_pid_and_executes_ioc(tmp_path: Path):
    root = _prepare_root(tmp_path)

    command = operator_commands._ioc_terminal_command(root, "172.22.50.2")

    assert "ioc.pid" in command
    assert 'printf \'%s\\n\' "$$"' in command
    assert "BDX_EPICS_INTERFACE=172.22.50.2" in command
    assert "exec " in command
    assert "bdx-prototype-ioc" in command


def test_archiver_precedes_phoebus_in_second_terminal(tmp_path: Path):
    root = _prepare_root(tmp_path)

    command = operator_commands._archiver_phoebus_terminal_command(
        root,
        "172.22.50.2",
        "overview",
        tmp_path / "phoebus",
    )

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


def test_start_raspberry_ioc_is_idempotent(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(operator_commands, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(
        operator_commands,
        "_caproto_get",
        lambda root: tmp_path / "caproto-get",
    )
    monkeypatch.setattr(
        operator_commands,
        "raspberry_ioc_responding",
        lambda command: True,
    )
    monkeypatch.setattr(
        operator_commands.shutil,
        "which",
        lambda name: (_ for _ in ()).throw(AssertionError("ssh should not run")),
    )

    operator_commands._start_raspberry_ioc([])

    assert "already responding" in capsys.readouterr().out


def test_start_raspberry_ioc_runs_ssh_and_waits(monkeypatch, tmp_path: Path):
    caproto_get = tmp_path / "caproto-get"
    responses = iter([False, True])
    captured = {}

    monkeypatch.setattr(operator_commands, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(
        operator_commands,
        "_caproto_get",
        lambda root: caproto_get,
    )
    monkeypatch.setattr(
        operator_commands,
        "raspberry_ioc_responding",
        lambda command: next(responses),
    )
    monkeypatch.setattr(operator_commands.shutil, "which", lambda name: "/usr/bin/ssh")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["check"] = kwargs["check"]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    operator_commands._start_raspberry_ioc(["--ssh-host", "pi@raspberry"])

    assert captured["command"][0] == "/usr/bin/ssh"
    assert captured["command"][1:3] == ["-t", "pi@raspberry"]
    assert "systemctl start bdx-environment-ioc" in captured["command"][3]
    assert captured["check"] is True


def test_direct_shutdown_command_executes_repository_script(monkeypatch, tmp_path: Path):
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "kill_slow_control_ioc.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    captured = {}

    monkeypatch.setattr(operator_commands, "_repository_root", lambda: root)

    def fake_execv(path, arguments):
        captured["path"] = path
        captured["arguments"] = arguments
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr(operator_commands.os, "execv", fake_execv)

    with pytest.raises(RuntimeError, match="exec intercepted"):
        operator_commands._exec_shutdown_script(
            "kill_slow_control_ioc.sh",
            ["--force"],
        )

    assert captured["path"] == str(script)
    assert captured["arguments"] == [str(script), "--force"]
