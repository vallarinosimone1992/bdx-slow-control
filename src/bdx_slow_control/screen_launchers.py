"""GNU Screen launchers for the BDX slow-control operator host."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
from typing import Sequence


DEFAULT_MAIN_SESSION = "bdx-slow-control"
DEFAULT_PHOEBUS_SESSION = "bdx-phoebus"
DEFAULT_RASPBERRY_HOST = "172.22.50.10"
DEFAULT_ARCHIVER_URL = "http://127.0.0.1:17668/retrieval"
DEFAULT_PHOEBUS_PREFLIGHT_PV = "BDX:ENV:TEMP:T00:VALUE"


class LauncherError(RuntimeError):
    """Raised for an operator-actionable launcher failure."""


def _repository_root() -> Path:
    override = os.environ.get("BDX_SLOW_CONTROL_ROOT")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())

    current = Path.cwd().resolve()
    candidates.extend([current, *current.parents])

    package_root = Path(__file__).resolve().parents[2]
    candidates.append(package_root)
    candidates.append(Path.home() / "SlowControl" / "app" / "bdx-slow-control")

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if (
            (candidate / "pyproject.toml").is_file()
            and (candidate / "scripts" / "launch_phoebus.sh").is_file()
            and (candidate / "deploy" / "archiver-appliance" / "scripts").is_dir()
        ):
            return candidate

    raise LauncherError(
        "BDX repository root not found. Set BDX_SLOW_CONTROL_ROOT to the "
        "bdx-slow-control checkout."
    )


def _require_program(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise LauncherError(f"Required program not found in PATH: {name}")
    return path


def _screen_session_exists(session: str) -> bool:
    screen = _require_program("screen")
    result = subprocess.run(
        [screen, "-ls"],
        text=True,
        capture_output=True,
        check=False,
    )
    pattern = re.compile(rf"\b\d+\.{re.escape(session)}\s")
    return bool(pattern.search(result.stdout + result.stderr))


def _attach_screen(session: str) -> None:
    screen = _require_program("screen")
    subprocess.run([screen, "-x", session], check=False)


def _start_screen_session(
    session: str,
    windows: Sequence[tuple[str, str]],
    *,
    select_window: str | None = None,
) -> None:
    if not windows:
        raise LauncherError("At least one screen window is required.")

    screen = _require_program("screen")
    first_title, first_command = windows[0]
    subprocess.run(
        [screen, "-DmS", session, "-t", first_title, "bash", "-lc", first_command],
        check=True,
    )

    try:
        for title, command in windows[1:]:
            subprocess.run(
                [
                    screen,
                    "-S",
                    session,
                    "-X",
                    "screen",
                    "-t",
                    title,
                    "bash",
                    "-lc",
                    command,
                ],
                check=True,
            )
        if select_window:
            subprocess.run(
                [screen, "-S", session, "-X", "select", select_window],
                check=False,
            )
    except Exception:
        subprocess.run([screen, "-S", session, "-X", "quit"], check=False)
        raise


def _read_main_host(root: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    environment_host = os.environ.get("BDX_MAIN_HOST", "").strip()
    if environment_host:
        return environment_host

    runtime_env = Path(
        os.environ.get("BDX_RUNTIME_ENV", root / "config" / "runtime.env")
    ).expanduser()
    if not runtime_env.is_file():
        raise LauncherError(
            f"Runtime environment not found: {runtime_env}. "
            "Create config/runtime.env with BDX_MAIN_HOST set."
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
        raise LauncherError(f"BDX_MAIN_HOST is not set in {runtime_env}.")
    return host


def _port_is_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _validate_slow_control_installation(root: Path) -> tuple[Path, Path]:
    ioc = root / ".venv" / "bin" / "bdx-prototype-ioc"
    archiver_env = Path(
        os.environ.get(
            "BDX_ARCHIVER_ENV",
            Path.home() / ".config" / "bdx-archiver" / "archappl.env",
        )
    ).expanduser()
    required = [
        ioc,
        archiver_env,
        root / "deploy" / "archiver-appliance" / "scripts" / "start.sh",
        root / "deploy" / "archiver-appliance" / "scripts" / "status.sh",
        root / "deploy" / "archiver-appliance" / "scripts" / "healthcheck.sh",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise LauncherError("Required installation path missing:\n  " + "\n  ".join(missing))
    return ioc, archiver_env


def _archiver_window_command(root: Path, host: str, archiver_env: Path) -> str:
    scripts = root / "deploy" / "archiver-appliance" / "scripts"
    start = scripts / "start.sh"
    status = scripts / "status.sh"
    healthcheck = scripts / "healthcheck.sh"
    q = shlex.quote

    body = f"""
(
    set -euo pipefail
    cd {q(str(root))}
    export BDX_MAIN_HOST={q(host)}
    export EPICS_CA_ADDR_LIST={q(f'{host} {DEFAULT_RASPBERRY_HOST}')}
    export EPICS_CA_AUTO_ADDR_LIST=NO

    status_output="$({q(str(status))} --env {q(str(archiver_env))} --user-local 2>&1 || true)"
    running_count="$(printf '%s\\n' "$status_output" | grep -Ec '^(mgmt|engine|etl|retrieval): running pid [0-9]+' || true)"

    case "$running_count" in
        0)
            echo "Starting the BDX Archiver Appliance."
            {q(str(start))} --env {q(str(archiver_env))} --user-local
            ;;
        4)
            echo "The BDX Archiver Appliance is already running."
            ;;
        *)
            printf '%s\\n' "$status_output" >&2
            echo "Archiver Appliance is only partially running; refusing to start duplicates." >&2
            exit 1
            ;;
    esac

    health_log="${{TMPDIR:-/tmp}}/bdx-archiver-health-${{USER}}.log"
    ready=0
    for _attempt in $(seq 1 90); do
        if {q(str(healthcheck))} --env {q(str(archiver_env))} --user-local >"$health_log" 2>&1; then
            ready=1
            break
        fi
        sleep 2
    done
    cat "$health_log" 2>/dev/null || true
    if [[ "$ready" -ne 1 ]]; then
        echo "Timed out waiting for the Archiver Appliance health check." >&2
        exit 1
    fi

    {q(str(status))} --env {q(str(archiver_env))} --user-local || true
    echo
    echo "Archiver window ready. The appliance processes continue in the background."
)
window_status=$?
echo
echo "Archiver launcher exited with status $window_status."
echo "This screen window remains open for inspection."
cd {q(str(root))}
exec bash
"""
    return body.strip()


def _ioc_window_command(root: Path, host: str, ioc: Path) -> str:
    q = shlex.quote
    body = f"""
(
    set -euo pipefail
    cd {q(str(root))}
    export BDX_MAIN_HOST={q(host)}
    export BDX_EPICS_INTERFACE={q(host)}
    export EPICS_CA_ADDR_LIST={q(f'{host} {DEFAULT_RASPBERRY_HOST}')}
    export EPICS_CA_AUTO_ADDR_LIST=NO
    export BDX_LOG_LEVEL="${{BDX_LOG_LEVEL:-INFO}}"
    {q(str(ioc))}
)
window_status=$?
echo
echo "IOC exited with status $window_status."
echo "This screen window remains open for inspection or a manual restart."
cd {q(str(root))}
exec bash
"""
    return body.strip()


def _phoebus_window_command(
    root: Path,
    display_name: str,
    phoebus_home: Path,
) -> str:
    launcher = root / "scripts" / "launch_phoebus.sh"
    q = shlex.quote
    return "\n".join(
        [
            f"cd {q(str(root))}",
            f"export BDX_PHOEBUS_HOME={q(str(phoebus_home))}",
            "export BDX_ARCHIVER_ENABLED=true",
            f"export BDX_ARCHIVER_URL={q(DEFAULT_ARCHIVER_URL)}",
            f"export BDX_ARCHIVER_PREFLIGHT_PV={q(DEFAULT_PHOEBUS_PREFLIGHT_PV)}",
            "export BDX_ARCHIVER_STRICT_CHECK=true",
            f"exec {q(str(launcher))} {q(display_name)}",
        ]
    )


def _resolve_phoebus_home(explicit: str | None) -> Path:
    raw = explicit or os.environ.get("BDX_PHOEBUS_HOME")
    if raw:
        home = Path(raw).expanduser().resolve()
    else:
        home = Path.home() / "SlowControl" / "css" / "phoebus-4.7.4-SNAPSHOT"
    launcher = home / "phoebus.sh"
    if not launcher.is_file():
        raise LauncherError(
            f"Phoebus launcher not found: {launcher}. "
            "Set BDX_PHOEBUS_HOME or pass --phoebus-home."
        )
    return home


def _run_cli(function, argv: Sequence[str] | None) -> None:
    try:
        function(argv)
    except LauncherError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed with status {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode) from exc


def _slow_control(argv: Sequence[str] | None) -> None:
    parser = argparse.ArgumentParser(
        prog="launch-bdx-slow-control",
        description="Launch the BDX Archiver and main IOC in two GNU Screen windows.",
    )
    parser.add_argument("--main-host", help="Override BDX_MAIN_HOST.")
    parser.add_argument("--session", default=DEFAULT_MAIN_SESSION)
    parser.add_argument("--attach", action="store_true")
    args = parser.parse_args(argv)

    root = _repository_root()
    _require_program("screen")

    if _screen_session_exists(args.session):
        print(f"Screen session already exists: {args.session}")
        if args.attach:
            _attach_screen(args.session)
        else:
            print(f"Attach with: screen -x {shlex.quote(args.session)}")
        return

    host = _read_main_host(root, args.main_host)
    if _port_is_listening(host, 5064):
        raise LauncherError(
            f"A Channel Access server is already listening on {host}:5064, but "
            f"screen session {args.session!r} does not exist. Stop the existing "
            "main IOC before moving it under screen."
        )

    ioc, archiver_env = _validate_slow_control_installation(root)
    windows = [
        ("archiver", _archiver_window_command(root, host, archiver_env)),
        ("ioc", _ioc_window_command(root, host, ioc)),
    ]
    _start_screen_session(args.session, windows, select_window="ioc")
    print(f"Started screen session: {args.session}")
    print("Windows: archiver, ioc")
    if args.attach:
        _attach_screen(args.session)
    else:
        print(f"Attach with: screen -x {shlex.quote(args.session)}")


def slow_control_main(argv: Sequence[str] | None = None) -> None:
    _run_cli(_slow_control, argv)


def _phoebus(argv: Sequence[str] | None) -> None:
    parser = argparse.ArgumentParser(
        prog="launch-bdx-phoebus",
        description="Launch Phoebus inside a dedicated GNU Screen session.",
    )
    parser.add_argument("display", nargs="?", default="overview")
    parser.add_argument("--session", default=DEFAULT_PHOEBUS_SESSION)
    parser.add_argument("--phoebus-home")
    parser.add_argument("--attach", action="store_true")
    args = parser.parse_args(argv)

    root = _repository_root()
    _require_program("screen")
    x_display = os.environ.get("DISPLAY", "").strip()
    if not x_display:
        raise LauncherError(
            "DISPLAY is empty. Launch Phoebus once from a terminal opened in the "
            "Ubuntu graphical session; the resulting screen session can then be "
            "inspected over SSH."
        )

    if _screen_session_exists(args.session):
        print(f"Phoebus screen session already exists: {args.session}")
        if args.attach:
            _attach_screen(args.session)
        else:
            print(f"Attach with: screen -x {shlex.quote(args.session)}")
        return

    phoebus_home = _resolve_phoebus_home(args.phoebus_home)
    command = _phoebus_window_command(root, args.display, phoebus_home)
    _start_screen_session(args.session, [("phoebus", command)])
    print(f"Started Phoebus in screen session: {args.session}")
    print(f"Graphical display: {x_display}")
    if args.attach:
        _attach_screen(args.session)
    else:
        print(f"Inspect logs with: screen -x {shlex.quote(args.session)}")


def phoebus_main(argv: Sequence[str] | None = None) -> None:
    _run_cli(_phoebus, argv)
