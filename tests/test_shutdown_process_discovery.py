from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
IOC_STOP = ROOT / "scripts/kill_slow_control_ioc.sh"
PHOEBUS_STOP = ROOT / "scripts/kill_slow_control_phoebus.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _kill_harness(script: Path) -> str:
    return "\n".join(
        [
            "kill() {",
            '  signal="$1"',
            '  pid="$2"',
            '  case "$signal" in',
            '    -0) [[ -f "$BDX_TEST_STATE/alive_$pid" ]] ;;',
            '    -TERM|-KILL)',
            '      printf "%s %s\\n" "$signal" "$pid" >> "$BDX_TEST_STATE/kill.log"',
            '      rm -f "$BDX_TEST_STATE/alive_$pid"',
            "      return 0",
            "      ;;",
            "    *) return 1 ;;",
            "  esac",
            "}",
            "export -f kill",
            f'bash "{script}"',
        ]
    )


def test_ioc_shutdown_discovers_unrecorded_ioc_without_touching_daq(tmp_path: Path):
    runtime = tmp_path / "runtime"
    state = tmp_path / "state"
    fake_bin = tmp_path / "bin"
    runtime.mkdir()
    state.mkdir()
    fake_bin.mkdir()
    ioc_alive = state / "alive_24680"
    ioc_alive.write_text("", encoding="utf-8")

    _write_executable(
        fake_bin / "ps",
        "\n".join(
            [
                'if [[ "$*" == *"-u "* ]]; then',
                '  echo "24680 /repo/.venv/bin/python /repo/.venv/bin/bdx-prototype-ioc"',
                '  echo "24681 /opt/bdx-daq/bin/bdx-daq --run"',
                "  exit 0",
                "fi",
                'if [[ "$*" == *"-p 24680"* && "$*" == *"-o stat="* ]]; then',
                f'  [[ -f "{ioc_alive}" ]] && echo "S"',
                "  exit 0",
                "fi",
                'if [[ "$*" == *"-p 24680"* && "$*" == *"-o command="* ]]; then',
                '  echo "/repo/.venv/bin/python /repo/.venv/bin/bdx-prototype-ioc"',
                "  exit 0",
                "fi",
                "exit 0",
            ]
        ),
    )

    result = subprocess.run(
        ["bash", "-c", _kill_harness(IOC_STOP)],
        check=True,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "BDX_STACK_RUNTIME_DIR": str(runtime),
            "BDX_TEST_STATE": str(state),
        },
    )

    assert "Stopped 1 BDX main IOC process(es)." in result.stdout
    kill_log = (state / "kill.log").read_text(encoding="utf-8")
    assert "-TERM 24680" in kill_log
    assert "24681" not in kill_log


def test_phoebus_shutdown_discovers_all_slow_control_sessions_only(tmp_path: Path):
    runtime = tmp_path / "runtime"
    state = tmp_path / "state"
    fake_bin = tmp_path / "bin"
    runtime.mkdir()
    state.mkdir()
    fake_bin.mkdir()
    for pid in (31001, 31002, 31003):
        (state / f"alive_{pid}").write_text("", encoding="utf-8")

    commands = {
        31001: (
            "java -jar /opt/phoebus/product-4.7.4-SNAPSHOT.jar "
            "-settings /run/user/1000/bdx-phoebus/settings.ini "
            "-resource /repo/bdx-slow-control/phoebus/displays/overview.bob"
        ),
        31002: (
            "java -jar /opt/phoebus/product-4.7.4-SNAPSHOT.jar "
            "-settings /tmp/custom.ini "
            "-resource /repo/bdx-slow-control/phoebus/displays/chiller.bob"
        ),
        31003: (
            "java -jar /opt/phoebus/product-4.7.4-SNAPSHOT.jar "
            "-settings /tmp/other/settings.ini -resource /tmp/other.bob"
        ),
    }

    ps_lines = [
        'if [[ "$*" == *"-u "* ]]; then',
        *[f'  echo "{pid} {command}"' for pid, command in commands.items()],
        '  echo "31004 /opt/bdx-daq/bin/bdx-daq --run"',
        "  exit 0",
        "fi",
    ]
    for pid, command in commands.items():
        alive_path = state / f"alive_{pid}"
        ps_lines.extend(
            [
                f'if [[ "$*" == *"-p {pid}"* && "$*" == *"-o stat="* ]]; then',
                f'  [[ -f "{alive_path}" ]] && echo "S"',
                "  exit 0",
                "fi",
                f'if [[ "$*" == *"-p {pid}"* && "$*" == *"-o command="* ]]; then',
                f'  echo "{command}"',
                "  exit 0",
                "fi",
            ]
        )
    ps_lines.append("exit 0")
    _write_executable(fake_bin / "ps", "\n".join(ps_lines))

    result = subprocess.run(
        ["bash", "-c", _kill_harness(PHOEBUS_STOP)],
        check=True,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "BDX_STACK_RUNTIME_DIR": str(runtime),
            "BDX_TEST_STATE": str(state),
        },
    )

    assert "Stopped 2 BDX slow-control Phoebus process(es)." in result.stdout
    kill_log = (state / "kill.log").read_text(encoding="utf-8")
    assert "-TERM 31001" in kill_log
    assert "-TERM 31002" in kill_log
    assert "31003" not in kill_log
    assert "31004" not in kill_log
