from pathlib import Path
import subprocess

from bdx_slow_control import operator_commands


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


def test_slow_control_warns_and_suggests_start_command(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    monkeypatch.setattr(
        operator_commands.screen_launchers,
        "_repository_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        operator_commands.screen_launchers,
        "_screen_session_exists",
        lambda session: False,
    )
    monkeypatch.setattr(
        operator_commands,
        "_caproto_get",
        lambda root: tmp_path / "caproto-get",
    )
    monkeypatch.setattr(
        operator_commands,
        "raspberry_ioc_responding",
        lambda command: False,
    )
    captured = {}

    def fake_slow_control(argv):
        captured["argv"] = argv

    monkeypatch.setattr(
        operator_commands.screen_launchers,
        "_slow_control",
        fake_slow_control,
    )

    operator_commands._slow_control([])

    output = capsys.readouterr()
    assert operator_commands.RASPBERRY_READY_PV in output.err
    assert "start-bdx-raspberry-ioc" in output.err
    assert "Continuing with the main IOC and Archiver startup" in output.err
    assert captured["argv"] == []


def test_slow_control_skips_probe_for_existing_screen_session(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(
        operator_commands.screen_launchers,
        "_repository_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        operator_commands.screen_launchers,
        "_screen_session_exists",
        lambda session: True,
    )
    monkeypatch.setattr(
        operator_commands,
        "_caproto_get",
        lambda root: (_ for _ in ()).throw(AssertionError("probe should be skipped")),
    )
    monkeypatch.setattr(
        operator_commands.screen_launchers,
        "_slow_control",
        lambda argv: None,
    )

    operator_commands._slow_control([])


def test_start_raspberry_ioc_is_idempotent(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(
        operator_commands.screen_launchers,
        "_repository_root",
        lambda: tmp_path,
    )
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

    monkeypatch.setattr(
        operator_commands.screen_launchers,
        "_repository_root",
        lambda: tmp_path,
    )
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
