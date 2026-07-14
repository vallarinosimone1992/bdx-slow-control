"""Operator commands for the BDX Ubuntu slow-control host."""

from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import time
from typing import Sequence

from .iocs.archiver_status import check_archiver_endpoints, summarize_results


DEFAULT_MAIN_HOST = "172.22.50.2"
RASPBERRY_HOST = "172.22.50.10"
RASPBERRY_READY_PV = "BDX:ENV:TEMP:T00:VALUE"
RASPBERRY_SERVICE = "bdx-environment-ioc"
DEFAULT_RASPBERRY_SSH_HOST = "pi@172.22.50.10"
DEFAULT_PHOEBUS_HOME = Path.home() / "SlowControl" / "css" / "phoebus-4.7.4-SNAPSHOT"
ARCHIVER_ENDPOINTS = {
    "mgmt": "http://127.0.0.1:17665/mgmt/bpl/getVersions",
    "engine": "http://127.0.0.1:17666/engine/bpl/getVersion",
    "etl": "http://127.0.0.1:17667/etl/bpl/getVersion",
    "retrieval": "http://127.0.0.1:17668/retrieval/bpl/getVersion",
}


class OperatorCommandError(RuntimeError):
    """Raised for an operator-actionable command failure."""


def _repository_root() -> Path:
    override = os.environ.get("BDX_SLOW_CONTROL_ROOT")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())

    current = Path.cwd().resolve()
    candidates.extend([current, *current.parents])
    candidates.append(Path(__file__).resolve().parents[2])
    candidates.append(Path.home() / "SlowControl" / "app" / "bdx-slow-control")

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if (
            (candidate / "pyproject.toml").is_file()
            and (candidate / "scripts" / "start_bdx_stack.sh").is_file()
            and (candidate / "scripts" / "launch_phoebus.sh").is_file()
        ):
            return candidate

    raise OperatorCommandError(
        "BDX repository root not found. Set BDX_SLOW_CONTROL_ROOT to the "
        "bdx-slow-control checkout."
    )


def _run_cli(function, argv: Sequence[str] | None) -> None:
    try:
        function(argv)
    except OperatorCommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed with status {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode) from exc


def _read_main_host(root: Path, explicit: str | None) -> str:
    if explicit:
        host = explicit
    elif os.environ.get("BDX_MAIN_HOST", "").strip():
        host = os.environ["BDX_MAIN_HOST"].strip()
    else:
        runtime_env = Path(
            os.environ.get("BDX_RUNTIME_ENV", root / "config" / "runtime.env")
        ).expanduser()
        if not runtime_env.is_file():
            raise OperatorCommandError(
                f"Runtime environment not found: {runtime_env}. "
                f"Create it with BDX_MAIN_HOST={DEFAULT_MAIN_HOST}."
            )
        command = (
            f"source {shlex.quote(str(runtime_env))}; "
            'printf "%s" "${BDX_MAIN_HOST:-}"'
        )
        result = subprocess.run(
            ["bash", "-lc", command],
            text=True,
            capture_output=True,
            check=False,
        )
        host = result.stdout.strip()
        if result.returncode != 0 or not host:
            raise OperatorCommandError(f"BDX_MAIN_HOST is not set in {runtime_env}.")

    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise OperatorCommandError(f"BDX_MAIN_HOST is not a valid IP address: {host}") from exc
    if address.is_unspecified or address.is_loopback:
        raise OperatorCommandError(
            f"BDX_MAIN_HOST must be the operational slow-control address, not {host}."
        )
    return host


def _port_is_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _caproto_get(root: Path) -> Path:
    command = root / ".venv" / "bin" / "caproto-get"
    if not command.is_file():
        raise OperatorCommandError(
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


def _runtime_dir(root: Path) -> Path:
    return Path(
        os.environ.get("BDX_STACK_RUNTIME_DIR", root / ".runtime" / "bdx-stack")
    ).expanduser()


def _recorded_process_running(pid_file: Path, markers: Sequence[str]) -> bool:
    def matches(command_line: str) -> bool:
        lowered = command_line.lower()
        return any(marker.lower() in lowered for marker in markers)

    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        pid = 0

    if pid:
        try:
            command_line = (
                (Path("/proc") / str(pid) / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode(errors="replace")
            )
        except OSError:
            command_line = ""
        if matches(command_line):
            return True
        pid_file.unlink(missing_ok=True)

    # The Phoebus shell launcher can fork the Java process before returning, so
    # reconcile the runtime PID with an exact BDX settings/display marker.
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        try:
            if proc_dir.stat().st_uid != os.getuid():
                continue
            command_line = (proc_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                errors="replace"
            )
        except OSError:
            continue
        if matches(command_line):
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(f"{proc_dir.name}\n", encoding="utf-8")
            return True
    return False


def _resolve_phoebus_home(explicit: str | None) -> Path:
    raw = explicit or os.environ.get("BDX_PHOEBUS_HOME")
    home = Path(raw).expanduser().resolve() if raw else DEFAULT_PHOEBUS_HOME
    launcher = home / "phoebus.sh"
    if not launcher.is_file():
        raise OperatorCommandError(
            f"Phoebus launcher not found: {launcher}. "
            "Set BDX_PHOEBUS_HOME or pass --phoebus-home."
        )
    return home


def _terminal_program() -> tuple[str, str]:
    explicit = os.environ.get("BDX_TERMINAL", "").strip()
    if explicit:
        path = shutil.which(explicit) or explicit
        if Path(path).is_file():
            return path, "xterm"
        raise OperatorCommandError(f"Configured terminal was not found: {explicit}")

    gnome_terminal = shutil.which("gnome-terminal")
    if gnome_terminal:
        return gnome_terminal, "gnome"

    generic_terminal = shutil.which("x-terminal-emulator")
    if generic_terminal:
        return generic_terminal, "xterm"

    raise OperatorCommandError(
        "No supported graphical terminal found. Install gnome-terminal or set BDX_TERMINAL."
    )


def _open_terminal(title: str, command: str) -> None:
    terminal, mode = _terminal_program()
    if mode == "gnome":
        invocation = [
            terminal,
            "--window",
            f"--title={title}",
            "--",
            "bash",
            "-lc",
            command,
        ]
    else:
        invocation = [terminal, "-T", title, "-e", "bash", "-lc", command]

    subprocess.Popen(
        invocation,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _ioc_terminal_command(root: Path, host: str) -> str:
    ioc = root / ".venv" / "bin" / "bdx-prototype-ioc"
    if not ioc.is_file():
        raise OperatorCommandError(f"BDX IOC command not found: {ioc}")

    runtime_dir = _runtime_dir(root)
    pid_file = runtime_dir / "ioc.pid"
    q = shlex.quote
    return "\n".join(
        [
            "set -euo pipefail",
            f"cd {q(str(root))}",
            f"mkdir -p {q(str(runtime_dir))}",
            f"printf '%s\\n' \"$$\" > {q(str(pid_file))}",
            f"export BDX_MAIN_HOST={q(host)}",
            f"export BDX_EPICS_INTERFACE={q(host)}",
            f"export EPICS_CA_ADDR_LIST={q(f'{host} {RASPBERRY_HOST}')} ",
            "export EPICS_CA_AUTO_ADDR_LIST=NO",
            'export BDX_LOG_LEVEL="${BDX_LOG_LEVEL:-INFO}"',
            'echo "Starting the BDX main IOC on $BDX_EPICS_INTERFACE:5064"',
            f"exec {q(str(ioc))}",
        ]
    )


def report_archiver_health(*, timeout: float = 0.5) -> str:
    """Inspect Archiver endpoints without mutating the independent service."""
    results = check_archiver_endpoints(ARCHIVER_ENDPOINTS, timeout)
    state, ok, details = summarize_results(results)
    if ok:
        message = "Archiver services: available and healthy."
    elif state in {"STARTING", "DEGRADED"}:
        message = f"Archiver services: starting or temporarily unavailable ({details})."
    else:
        message = "Archiver services: completely absent. Historical data is unavailable."
    print(message)
    return state


def _wait_for_raspberry_ioc(
    caproto_get: Path,
    timeout: int,
    ssh_host: str,
) -> None:
    deadline = time.monotonic() + timeout
    print(f"Waiting for Raspberry IOC PV: {RASPBERRY_READY_PV}")
    while time.monotonic() < deadline:
        if raspberry_ioc_responding(caproto_get):
            print(f"Raspberry environment IOC: responding ({RASPBERRY_READY_PV})")
            return
        time.sleep(1)
    raise OperatorCommandError(
        f"Timed out after {timeout} seconds waiting for {RASPBERRY_READY_PV}. "
        f"Inspect the remote service with: ssh -t {ssh_host} "
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
        raise OperatorCommandError("--timeout must be at least 1 second.")

    root = _repository_root()
    caproto_get = _caproto_get(root)

    if not args.restart and raspberry_ioc_responding(caproto_get):
        print(f"Raspberry environment IOC is already responding: {RASPBERRY_READY_PV}")
        return

    ssh = shutil.which("ssh")
    if ssh is None:
        raise OperatorCommandError("Required program not found in PATH: ssh")

    action = "restart" if args.restart else "start"
    remote_command = (
        f"sudo systemctl {action} {RASPBERRY_SERVICE} && "
        f"sudo systemctl --no-pager --full status {RASPBERRY_SERVICE}"
    )
    print(f"Running on {args.ssh_host}: systemctl {action} {RASPBERRY_SERVICE}")
    subprocess.run([ssh, "-t", args.ssh_host, remote_command], check=True)
    _wait_for_raspberry_ioc(caproto_get, args.timeout, args.ssh_host)


def raspberry_ioc_main(argv: Sequence[str] | None = None) -> None:
    _run_cli(_start_raspberry_ioc, argv)


def _exec_shutdown_script(script_name: str, argv: Sequence[str] | None) -> None:
    root = _repository_root()
    script = root / "scripts" / script_name
    if not script.is_file():
        raise OperatorCommandError(f"Shutdown script not found: {script}")
    arguments = list(sys.argv[1:] if argv is None else argv)
    os.execv(str(script), [str(script), *arguments])


def kill_ioc_main(argv: Sequence[str] | None = None) -> None:
    _run_cli(lambda values: _exec_shutdown_script("kill_slow_control_ioc.sh", values), argv)


def kill_archiver_main(argv: Sequence[str] | None = None) -> None:
    _run_cli(
        lambda values: _exec_shutdown_script("kill_slow_control_archiver.sh", values),
        argv,
    )


def slow_control_kill_main(argv: Sequence[str] | None = None) -> None:
    _run_cli(
        lambda values: _exec_shutdown_script("kill_slow_control_all.sh", values),
        argv,
    )


def archiver_kill_main(argv: Sequence[str] | None = None) -> None:
    kill_archiver_main(argv)


def kill_phoebus_main(argv: Sequence[str] | None = None) -> None:
    _run_cli(
        lambda values: _exec_shutdown_script("kill_slow_control_phoebus.sh", values),
        argv,
    )
