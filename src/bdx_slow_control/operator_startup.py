"""Independent Ubuntu lifecycle commands for slow control and the Archiver."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Sequence

from . import operator_commands as common


def _phoebus_terminal_command(
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
            "(",
            "  set -euo pipefail",
            f"  source {q(str(stack_script))}",
            f"  bdx_stack_parse_args --main-host {q(host)} {q(display)}",
            "  bdx_stack_load_runtime_environment",
            "  bdx_stack_validate_slow_control_installation",
            "  bdx_stack_print_summary",
            "  bdx_stack_wait_for_ioc_listener 90",
            '  bdx_stack_wait_for_pv_read "$IOC_READY_PV" 90',
            "  bdx_stack_report_archiver_status",
            '  bdx_stack_launch_phoebus "$BDX_STACK_DISPLAY"',
            ")",
            "status=$?",
            "echo",
            "if (( status != 0 )); then",
            (
                '  echo "ERROR: Phoebus startup failed after IOC readiness." >&2'
            ),
            "fi",
            'echo "Phoebus workflow exited with status $status."',
            'echo "This terminal remains open for inspection."',
            "exec bash",
        ]
    )


def _start_slow_control(argv: Sequence[str] | None) -> None:
    parser = argparse.ArgumentParser(
        prog="bdx_slow_control_start",
        description=(
            "Start the main IOC, verify readiness, and launch Phoebus. The independent "
            "Archiver is inspected read-only and is never started or repaired."
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

    common.report_archiver_health()

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
        ("bdx-phoebus/settings.ini", "bdx-slow-control/phoebus/displays/"),
    ):
        print("Phoebus is already running; not opening another instance.")
    else:
        common._open_terminal(
            "BDX Phoebus",
            _phoebus_terminal_command(
                root,
                host,
                args.display,
                phoebus_home,
            ),
        )
        print("Opened terminal: BDX Phoebus")


def slow_control_start_main(argv: Sequence[str] | None = None) -> None:
    common._run_cli(_start_slow_control, argv)


def _start_archiver(argv: Sequence[str] | None) -> None:
    parser = argparse.ArgumentParser(
        prog="bdx_archiver_start",
        description="Start and fully validate only the Archiver Appliance.",
    )
    parser.add_argument("--main-host")
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Bound component post-startup readiness in seconds (default: 180).",
    )
    parser.add_argument(
        "--no-repair",
        action="store_true",
        help="Start and validate components without catalog audit/repair.",
    )
    args = parser.parse_args(argv)
    if args.timeout < 1:
        raise common.OperatorCommandError("--timeout must be at least 1 second.")
    root = common._repository_root()
    host = common._read_main_host(root, args.main_host)
    stack_script = root / "scripts" / "start_bdx_stack.sh"
    q = shlex.quote
    command = "\n".join(
        [
            "set -euo pipefail",
            f"source {q(str(stack_script))}",
            f"bdx_stack_parse_args --main-host {q(host)}",
            "bdx_stack_load_runtime_environment",
            "bdx_stack_validate_archiver_installation",
            "bdx_stack_print_summary",
            f"bdx_stack_start_and_validate_archiver {args.timeout} "
            f"{'false' if args.no_repair else 'true'}",
        ]
    )
    subprocess.run(["bash", "-c", command], cwd=root, check=True)


def start_archiver_main(argv: Sequence[str] | None = None) -> None:
    common._run_cli(_start_archiver, argv)


def _repair_archiver(argv: Sequence[str] | None) -> None:
    repair_args = list(argv) if argv is not None else sys.argv[1:]
    root = common._repository_root()
    script = root / "deploy" / "archiver-appliance" / "scripts" / "repair-archiver.sh"
    env_file = Path.home() / ".config" / "bdx-archiver" / "archappl.env"
    if any(arg in {"-h", "--help"} for arg in repair_args):
        subprocess.run([str(script), "--help"], cwd=root, check=True)
        return
    subprocess.run(
        [str(script), "--env", str(env_file), "--user-local", "--", *repair_args],
        cwd=root,
        check=True,
    )


def repair_archiver_main(argv: Sequence[str] | None = None) -> None:
    common._run_cli(_repair_archiver, argv)


def audit_archiver_main(argv: Sequence[str] | None = None) -> None:
    values = list(argv) if argv is not None else sys.argv[1:]
    common._run_cli(_repair_archiver, ["--audit-only", *values])
