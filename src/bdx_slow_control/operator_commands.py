"""Operator commands that coordinate the main host and Raspberry IOC."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Sequence

from . import screen_launchers


RASPBERRY_HOST = "172.22.50.10"
RASPBERRY_READY_PV = "BDX:ENV:TEMP:T00:VALUE"
RASPBERRY_SERVICE = "bdx-environment-ioc"
DEFAULT_RASPBERRY_SSH_HOST = "pi@172.22.50.10"


def _caproto_get(root: Path) -> Path:
    command = root / ".venv" / "bin" / "caproto-get"
    if not command.is_file():
        raise screen_launchers.LauncherError(
            f"caproto-get not found: {command}. Run scripts/bootstrap.sh first."
        )
    return command


def raspberry_ioc_responding(caproto_get: Path, *, timeout: float = 2.0) -> bool:
    """Return whether the Raspberry environment IOC serves its readiness PV."""
    environment = os.environ.copy()
    environment["EPICS_CA_ADDR_LIST"] = RASPBERRY_HOST
    environment["EPICS_CA_AUTO_ADDR_LIST"] = "NO"
    try:
        result = subprocess.run(
            [str(caproto_get), "--timeout", str(timeout), RASPBERRY_READY_PV],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout + 3.0,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def _parse_slow_control_probe_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--main-host")
    parser.add_argument("--session", default=screen_launchers.DEFAULT_MAIN_SESSION)
    parser.add_argument("--attach", action="store_true")
    args, _unknown = parser.parse_known_args(argv)
    return args


def _slow_control(argv: Sequence[str] | None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "-h" in arguments or "--help" in arguments:
        screen_launchers._slow_control(arguments)
        return

    args = _parse_slow_control_probe_args(arguments)
    root = screen_launchers._repository_root()

    if not screen_launchers._screen_session_exists(args.session):
        caproto_get = _caproto_get(root)
        if raspberry_ioc_responding(caproto_get):
            print(f"Raspberry environment IOC: responding ({RASPBERRY_READY_PV})")
        else:
            print(
                f"Warning: Raspberry environment IOC is not responding: "
                f"{RASPBERRY_READY_PV}",
                file=sys.stderr,
            )
            print(
                "Start it with: start-bdx-raspberry-ioc",
                file=sys.stderr,
            )
            print(
                "Continuing with the main IOC and Archiver startup.",
                file=sys.stderr,
            )

    screen_launchers._slow_control(arguments)


def slow_control_main(argv: Sequence[str] | None = None) -> None:
    screen_launchers._run_cli(_slow_control, argv)


def _wait_for_raspberry_ioc(caproto_get: Path, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    print(f"Waiting for Raspberry IOC PV: {RASPBERRY_READY_PV}")
    while time.monotonic() < deadline:
        if raspberry_ioc_responding(caproto_get):
            print(f"Raspberry environment IOC: responding ({RASPBERRY_READY_PV})")
            return
        time.sleep(1)
    raise screen_launchers.LauncherError(
        f"Timed out after {timeout} seconds waiting for {RASPBERRY_READY_PV}. "
        f"Inspect the remote service with: ssh -t "
        f"{os.environ.get('BDX_RASPBERRY_SSH_HOST', DEFAULT_RASPBERRY_SSH_HOST)} "
        f"sudo journalctl -u {RASPBERRY_SERVICE} -n 100 --no-pager"
    )


def _start_raspberry_ioc(argv: Sequence[str] | None) -> None:
    parser = argparse.ArgumentParser(
        prog="start-bdx-raspberry-ioc",
        description="Start the Raspberry environment IOC through SSH and verify Channel Access.",
    )
    parser.add_argument(
        "--ssh-host",
        default=os.environ.get(
            "BDX_RASPBERRY_SSH_HOST", DEFAULT_RASPBERRY_SSH_HOST
        ),
        help=(
            "SSH destination, including the user when needed "
            f"(default: {DEFAULT_RASPBERRY_SSH_HOST})."
        ),
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Restart the service even when it may already be active.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Seconds to wait for the readiness PV after systemctl (default: 30).",
    )
    args = parser.parse_args(argv)

    if args.timeout < 1:
        raise screen_launchers.LauncherError("--timeout must be at least 1 second.")

    root = screen_launchers._repository_root()
    caproto_get = _caproto_get(root)

    if not args.restart and raspberry_ioc_responding(caproto_get):
        print(f"Raspberry environment IOC is already responding: {RASPBERRY_READY_PV}")
        return

    ssh = shutil.which("ssh")
    if ssh is None:
        raise screen_launchers.LauncherError("Required program not found in PATH: ssh")

    action = "restart" if args.restart else "start"
    remote_command = (
        f"sudo systemctl {action} {RASPBERRY_SERVICE} && "
        f"sudo systemctl --no-pager --full status {RASPBERRY_SERVICE}"
    )
    print(f"Running on {args.ssh_host}: systemctl {action} {RASPBERRY_SERVICE}")
    subprocess.run(
        [ssh, "-t", args.ssh_host, remote_command],
        check=True,
    )
    _wait_for_raspberry_ioc(caproto_get, args.timeout)


def raspberry_ioc_main(argv: Sequence[str] | None = None) -> None:
    screen_launchers._run_cli(_start_raspberry_ioc, argv)
