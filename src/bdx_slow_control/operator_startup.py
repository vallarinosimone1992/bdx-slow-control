"""Ubuntu startup command with controlled Archiver registration."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import sys
from typing import Sequence

from . import operator_commands as common


def _archiver_phoebus_terminal_command(
    root: Path,
    host: str,
    display: str,
    phoebus_home: Path,
) -> str:
    stack_script = root / "scripts" / "start_bdx_stack.sh"
    q = shlex.quote
    return "\n".join(
        [
            f"cd {q(str(root))}",
            f"export BDX_PHOEBUS_HOME={q(str(phoebus_home))}",
            "export BDX_ARCHIVER_STRICT_CHECK=true",
            "(",
            "  set -euo pipefail",
            f"  source {q(str(stack_script))}",
            f"  bdx_stack_parse_args --main-host {q(host)} {q(display)}",
            "  bdx_stack_load_runtime_environment",
            "  bdx_stack_validate_installation",
            "  bdx_stack_print_summary",
            "  bdx_stack_wait_for_ioc_listener 90",
            '  bdx_stack_wait_for_pv_read "$IOC_READY_PV" 90',
            "  bdx_stack_ensure_archiver",
            '  echo "Starting controlled Archiver PV registration and validation."',
            "  bdx_stack_controlled_archiver_registration",
            (
                '  echo "Archiver registration and representative-PV validation '
                'completed successfully."'
            ),
            '  bdx_stack_launch_phoebus "$BDX_STACK_DISPLAY"',
            ")",
            "status=$?",
            "echo",
            "if (( status != 0 )); then",
            (
                '  echo "ERROR: Archiver/Phoebus startup failed. Phoebus launch is '
                'blocked until Archiver registration and representative-PV validation '
                'succeed." >&2'
            ),
            "fi",
            'echo "Archiver/Phoebus workflow exited with status $status."',
            'echo "This terminal remains open for inspection."',
            "exec bash",
        ]
    )


def _start_slow_control(argv: Sequence[str] | None) -> None:
    parser = argparse.ArgumentParser(
        prog="bdx_slow_control_start",
        description=(
            "Open one terminal for the main IOC and one terminal for Archiver startup "
            "followed by Phoebus."
        ),
    )
    parser.add_argument("display", nargs="?", default="overview")
    parser.add_argument("--main-host")
    parser.add_argument("--phoebus-home")
    args = parser.parse_args(argv)

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        raise common.OperatorCommandError(
            "No graphical desktop display is available. Run this command from a terminal "
            "opened in the Ubuntu desktop session."
        )

    root = common._repository_root()
    host = common._read_main_host(root, args.main_host)
    caproto_get = common._caproto_get(root)
    phoebus_home = common._resolve_phoebus_home(args.phoebus_home)
    common._terminal_program()

    if common.raspberry_ioc_responding(caproto_get):
        print(f"Raspberry environment IOC: responding ({common.RASPBERRY_READY_PV})")
    else:
        print(
            "Warning: Raspberry environment IOC is not responding: "
            f"{common.RASPBERRY_READY_PV}",
            file=sys.stderr,
        )
        print("Start it with: start-bdx-raspberry-ioc", file=sys.stderr)
        print("Continuing with the local slow-control startup.", file=sys.stderr)

    if common._port_is_listening(host, 5064):
        print(f"BDX main IOC is already listening on {host}:5064; not opening another IOC.")
    else:
        common._open_terminal("BDX Main IOC", common._ioc_terminal_command(root, host))
        print("Opened terminal: BDX Main IOC")

    phoebus_pid_file = common._runtime_dir(root) / "phoebus.pid"
    if common._recorded_process_running(
        phoebus_pid_file,
        ("phoebus", "org.phoebus", "javafx"),
    ):
        print("Phoebus is already running; not opening another instance.")
    else:
        common._open_terminal(
            "BDX Archiver and Phoebus",
            _archiver_phoebus_terminal_command(
                root,
                host,
                args.display,
                phoebus_home,
            ),
        )
        print("Opened terminal: BDX Archiver and Phoebus")


def slow_control_start_main(argv: Sequence[str] | None = None) -> None:
    common._run_cli(_start_slow_control, argv)
