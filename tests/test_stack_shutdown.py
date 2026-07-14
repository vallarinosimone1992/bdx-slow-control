import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
IOC_STOP = ROOT / "scripts/kill_slow_control_ioc.sh"
PHOEBUS_STOP = ROOT / "scripts/kill_slow_control_phoebus.sh"
ARCHIVER_STOP = ROOT / "scripts/kill_slow_control_archiver.sh"
ALL_STOP = ROOT / "scripts/kill_slow_control_all.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _run_script(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        check=check,
        text=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
    )


def _make_fake_ps(tmp_path: Path, command_line: str) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    ps_script = fake_bin / "ps"
    _write_executable(
        ps_script,
        "\n".join(
            [
                'if [[ "$*" == *"-o stat="* ]]; then',
                f'  if [[ -f "{tmp_path / "alive"}" ]]; then echo "S"; fi',
                "  exit 0",
                "fi",
                f'printf "%s\\n" "{command_line}"',
            ]
        ),
    )
    return fake_bin


def _mocked_kill_harness(
    script: Path,
    tmp_path: Path,
    *args: str,
    term_removes_alive: bool = True,
) -> str:
    alive = tmp_path / "alive"
    kill_log = tmp_path / "kill.log"
    term_action = f'rm -f "{alive}"' if term_removes_alive else ":"
    rendered_args = " ".join(subprocess.list2cmdline([arg]) for arg in args)
    return "\n".join(
        [
            "kill() {",
            f'  printf "%s\\n" "$*" >> "{kill_log}"',
            '  if [[ "$1" == "-0" ]]; then',
            f'    [[ -f "{alive}" ]]',
            "    return $?",
            "  fi",
            '  if [[ "$1" == "-TERM" ]]; then',
            f"    {term_action}",
            "    return 0",
            "  fi",
            '  if [[ "$1" == "-KILL" ]]; then',
            f'    rm -f "{alive}"',
            "    return 0",
            "  fi",
            "  return 0",
            "}",
            "export -f kill",
            f'bash "{script}" {rendered_args}',
        ]
    )


def test_ioc_shutdown_handles_valid_pid_with_sigterm(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "ioc.pid").write_text("12345\n", encoding="utf-8")
    (tmp_path / "alive").write_text("", encoding="utf-8")
    fake_bin = _make_fake_ps(tmp_path, "/repo/.venv/bin/bdx-prototype-ioc")

    result = _run_script(
        ["bash", "-c", _mocked_kill_harness(IOC_STOP, tmp_path)],
        env={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "BDX_STACK_RUNTIME_DIR": str(runtime),
        },
    )

    assert "BDX main IOC stopped" in result.stdout
    assert not (runtime / "ioc.pid").exists()
    assert "-TERM 12345" in (tmp_path / "kill.log").read_text(encoding="utf-8")


def test_ioc_shutdown_removes_stale_pid_file(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "ioc.pid").write_text("12345\n", encoding="utf-8")
    fake_bin = _make_fake_ps(tmp_path, "/repo/.venv/bin/bdx-prototype-ioc")

    result = _run_script(
        ["bash", "-c", _mocked_kill_harness(IOC_STOP, tmp_path)],
        env={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "BDX_STACK_RUNTIME_DIR": str(runtime),
        },
    )

    assert "already stopped" in result.stdout
    assert not (runtime / "ioc.pid").exists()


def test_ioc_shutdown_refuses_reused_pid_for_unrelated_process(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "ioc.pid").write_text("12345\n", encoding="utf-8")
    (tmp_path / "alive").write_text("", encoding="utf-8")
    fake_bin = _make_fake_ps(tmp_path, "/usr/bin/python unrelated.py")

    result = _run_script(
        ["bash", "-c", _mocked_kill_harness(IOC_STOP, tmp_path)],
        env={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "BDX_STACK_RUNTIME_DIR": str(runtime),
        },
        check=False,
    )

    assert result.returncode == 2
    assert "Refusing to stop PID 12345" in result.stderr
    assert (runtime / "ioc.pid").exists()
    assert "-TERM 12345" not in (tmp_path / "kill.log").read_text(encoding="utf-8")


def test_ioc_shutdown_uses_sigkill_only_with_force(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "ioc.pid").write_text("12345\n", encoding="utf-8")
    (tmp_path / "alive").write_text("", encoding="utf-8")
    fake_bin = _make_fake_ps(tmp_path, "/repo/.venv/bin/bdx-prototype-ioc")

    result = _run_script(
        ["bash", "-c", _mocked_kill_harness(IOC_STOP, tmp_path, "--timeout", "0", "--force", term_removes_alive=False)],
        env={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "BDX_STACK_RUNTIME_DIR": str(runtime),
        },
    )

    kill_log = (tmp_path / "kill.log").read_text(encoding="utf-8")
    assert result.returncode == 0
    assert "-TERM 12345" in kill_log
    assert "-KILL 12345" in kill_log
    assert not (runtime / "ioc.pid").exists()


def test_phoebus_direct_shutdown_validates_and_terminates_recorded_pid(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "phoebus.pid").write_text("54321\n", encoding="utf-8")
    (runtime / "phoebus.mode").write_text("direct\n", encoding="utf-8")
    (tmp_path / "alive").write_text("", encoding="utf-8")
    fake_bin = _make_fake_ps(tmp_path, "/opt/phoebus/phoebus.sh -settings settings.ini")

    result = _run_script(
        ["bash", "-c", _mocked_kill_harness(PHOEBUS_STOP, tmp_path)],
        env={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "BDX_STACK_RUNTIME_DIR": str(runtime),
        },
    )

    assert "Phoebus stopped" in result.stdout
    assert not (runtime / "phoebus.pid").exists()
    assert not (runtime / "phoebus.mode").exists()
    assert "-TERM 54321" in (tmp_path / "kill.log").read_text(encoding="utf-8")


def test_phoebus_macos_app_shutdown_uses_osascript(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "phoebus.mode").write_text("macos-app\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    osascript_log = tmp_path / "osascript.log"
    _write_executable(
        fake_bin / "osascript",
        "\n".join(
            [
                f'printf "%s\\n" "$*" >> "{osascript_log}"',
                'if [[ "$*" == *"System Events"* ]]; then echo true; fi',
            ]
        ),
    )

    result = _run_script(
        ["bash", str(PHOEBUS_STOP)],
        env={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "BDX_STACK_RUNTIME_DIR": str(runtime),
        },
    )

    assert "active macOS Phoebus application instance" in result.stdout
    assert "tell application Phoebus to quit" in osascript_log.read_text(
        encoding="utf-8"
    ).replace('"', "")
    assert not (runtime / "phoebus.mode").exists()


def test_phoebus_shutdown_returns_success_when_already_stopped(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    result = _run_script(
        ["bash", str(PHOEBUS_STOP)],
        env={"BDX_STACK_RUNTIME_DIR": str(runtime)},
    )

    assert "Phoebus is already stopped" in result.stdout


def test_shutdown_scripts_do_not_use_generic_process_kill_patterns():
    scripts = [
        IOC_STOP,
        PHOEBUS_STOP,
        ARCHIVER_STOP,
        ALL_STOP,
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in scripts)

    assert "pkill" not in combined
    assert "killall" not in combined
    assert "pkill java" not in combined.lower()
    assert "pkill python" not in combined.lower()


def test_archiver_shutdown_invokes_installed_stop_script(tmp_path: Path):
    app_dir = tmp_path / "app"
    scripts_dir = app_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    env_file = tmp_path / "archappl.env"
    env_file.write_text("", encoding="utf-8")
    state_file = tmp_path / "running"
    state_file.write_text("", encoding="utf-8")
    stop_log = tmp_path / "stop.log"

    _write_executable(
        scripts_dir / "status.sh",
        "\n".join(
            [
                f'if [[ -f "{state_file}" ]]; then',
                '  echo "mgmt: running pid 1"',
                '  echo "engine: running pid 2"',
                '  echo "etl: running pid 3"',
                '  echo "retrieval: running pid 4"',
                "  exit 0",
                "fi",
                'echo "mgmt: not running"',
                'echo "engine: not running"',
                'echo "etl: not running"',
                'echo "retrieval: not running"',
                "exit 1",
            ]
        ),
    )
    _write_executable(
        scripts_dir / "stop.sh",
        "\n".join(
            [
                f'printf "%s\\n" "$*" > "{stop_log}"',
                f'rm -f "{state_file}"',
            ]
        ),
    )

    result = _run_script(
        ["bash", str(ARCHIVER_STOP)],
        env={
            "BDX_ARCHIVER_APP_DIR": str(app_dir),
            "BDX_ARCHIVER_ENV_FILE": str(env_file),
        },
    )

    assert "Archiver Appliance stopped" in result.stdout
    assert f"--env {env_file} --user-local" in stop_log.read_text(encoding="utf-8")


def test_archiver_shutdown_is_successful_when_already_stopped(tmp_path: Path):
    app_dir = tmp_path / "app"
    scripts_dir = app_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    env_file = tmp_path / "archappl.env"
    env_file.write_text("", encoding="utf-8")
    stop_log = tmp_path / "stop.log"

    _write_executable(
        scripts_dir / "status.sh",
        "\n".join(
            [
                'echo "mgmt: not running"',
                'echo "engine: not running"',
                'echo "etl: not running"',
                'echo "retrieval: not running"',
                "exit 1",
            ]
        ),
    )
    _write_executable(
        scripts_dir / "stop.sh",
        f'printf "%s\\n" "$*" > "{stop_log}"',
    )

    result = _run_script(
        ["bash", str(ARCHIVER_STOP)],
        env={
            "BDX_ARCHIVER_APP_DIR": str(app_dir),
            "BDX_ARCHIVER_ENV_FILE": str(env_file),
        },
    )

    assert "Archiver Appliance stopped" in result.stdout
    assert stop_log.exists()


def test_normal_slow_control_shutdown_excludes_archiver(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    app_dir = tmp_path / "app"
    scripts_dir = app_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    env_file = tmp_path / "archappl.env"
    env_file.write_text("", encoding="utf-8")

    _write_executable(
        scripts_dir / "status.sh",
        "\n".join(
            [
                'echo "mgmt: not running"',
                'echo "engine: not running"',
                'echo "etl: not running"',
                'echo "retrieval: not running"',
                "exit 1",
            ]
        ),
    )
    _write_executable(scripts_dir / "stop.sh", "exit 0")

    result = _run_script(
        ["bash", str(ALL_STOP)],
        env={
            "BDX_STACK_RUNTIME_DIR": str(runtime),
            "BDX_ARCHIVER_APP_DIR": str(app_dir),
            "BDX_ARCHIVER_ENV_FILE": str(env_file),
        },
    )

    phoebus_index = result.stdout.index("Stopping Phoebus...")
    ioc_index = result.stdout.index("Stopping BDX main IOC...")
    assert phoebus_index < ioc_index
    assert "Archiver Appliance" not in result.stdout
