from pathlib import Path
import subprocess

import pytest

from bdx_slow_control import screen_launchers as launchers


def test_screen_session_detection(monkeypatch):
    monkeypatch.setattr(launchers, "_require_program", lambda name: "/usr/bin/screen")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            "There is a screen on:\n\t1234.bdx-slow-control\t(Detached)\n",
            "",
        ),
    )

    assert launchers._screen_session_exists("bdx-slow-control") is True
    assert launchers._screen_session_exists("bdx-phoebus") is False


def test_slow_control_creates_archiver_and_ioc_windows(monkeypatch, tmp_path: Path):
    root = tmp_path
    ioc = root / ".venv/bin/bdx-prototype-ioc"
    archiver_env = tmp_path / "archappl.env"
    captured = {}

    monkeypatch.setattr(launchers, "_repository_root", lambda: root)
    monkeypatch.setattr(launchers, "_require_program", lambda name: "/usr/bin/screen")
    monkeypatch.setattr(launchers, "_screen_session_exists", lambda session: False)
    monkeypatch.setattr(
        launchers,
        "_read_main_host",
        lambda root, explicit: "172.22.50.2",
    )
    monkeypatch.setattr(launchers, "_port_is_listening", lambda host, port: False)
    monkeypatch.setattr(
        launchers,
        "_validate_slow_control_installation",
        lambda root: (ioc, archiver_env),
    )

    def fake_start(session, windows, *, select_window=None):
        captured["session"] = session
        captured["windows"] = windows
        captured["select_window"] = select_window

    monkeypatch.setattr(launchers, "_start_screen_session", fake_start)
    launchers._slow_control([])

    assert captured["session"] == "bdx-slow-control"
    assert [title for title, _ in captured["windows"]] == ["archiver", "ioc"]
    assert captured["select_window"] == "ioc"
    assert "start.sh" in captured["windows"][0][1]
    assert "bdx-prototype-ioc" in captured["windows"][1][1]


def test_slow_control_refuses_duplicate_listener_without_screen(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(launchers, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(launchers, "_require_program", lambda name: "/usr/bin/screen")
    monkeypatch.setattr(launchers, "_screen_session_exists", lambda session: False)
    monkeypatch.setattr(
        launchers,
        "_read_main_host",
        lambda root, explicit: "172.22.50.2",
    )
    monkeypatch.setattr(launchers, "_port_is_listening", lambda host, port: True)

    with pytest.raises(launchers.LauncherError, match="already listening"):
        launchers._slow_control([])


def test_phoebus_requires_graphical_display(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(launchers, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(launchers, "_require_program", lambda name: "/usr/bin/screen")
    monkeypatch.delenv("DISPLAY", raising=False)

    with pytest.raises(launchers.LauncherError, match="DISPLAY is empty"):
        launchers._phoebus([])


def test_phoebus_uses_dedicated_screen_session(monkeypatch, tmp_path: Path):
    phoebus_home = tmp_path / "phoebus"
    captured = {}

    monkeypatch.setattr(launchers, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(launchers, "_require_program", lambda name: "/usr/bin/screen")
    monkeypatch.setattr(launchers, "_screen_session_exists", lambda session: False)
    monkeypatch.setattr(
        launchers,
        "_resolve_phoebus_home",
        lambda explicit: phoebus_home,
    )
    monkeypatch.setenv("DISPLAY", ":0")

    def fake_start(session, windows, *, select_window=None):
        captured["session"] = session
        captured["windows"] = windows

    monkeypatch.setattr(launchers, "_start_screen_session", fake_start)
    launchers._phoebus(["overview"])

    assert captured["session"] == "bdx-phoebus"
    assert [title for title, _ in captured["windows"]] == ["phoebus"]
    command = captured["windows"][0][1]
    assert "launch_phoebus.sh overview" in command
    assert "BDX_ARCHIVER_STRICT_CHECK=true" in command
