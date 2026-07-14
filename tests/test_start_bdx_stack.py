import os
from pathlib import Path
import re
import subprocess


START_SCRIPT = Path("scripts/start_bdx_stack.sh").resolve()
PHOEBUS_SCRIPT = Path("scripts/launch_phoebus.sh")
ARCHIVER_COMMON = Path("deploy/archiver-appliance/scripts/common.sh").resolve()


def _run_bash(
    script: str,
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        ["bash", "-c", script],
        check=check,
        text=True,
        capture_output=True,
        env=merged_env,
    )


def _write_fake_script(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _archiver_url_exports() -> str:
    return "\n".join(
        [
            "BDX_ARCHIVER_MGMT_URL=http://127.0.0.1:17665/mgmt/bpl",
            "BDX_ARCHIVER_ENGINE_URL=http://127.0.0.1:17666/engine/bpl",
            "BDX_ARCHIVER_ETL_URL=http://127.0.0.1:17667/etl/bpl",
            "BDX_ARCHIVER_RETRIEVAL_BPL_URL=http://127.0.0.1:17668/retrieval/bpl",
        ]
    )


def test_missing_runtime_env_and_no_environment_host_fails(tmp_path: Path):
    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                "bdx_stack_parse_args overview",
                "bdx_stack_load_runtime_environment",
            ]
        ),
        env={
            "BDX_RUNTIME_ENV": str(tmp_path / "missing.env"),
            "BDX_MAIN_HOST": "",
        },
        check=False,
    )

    assert result.returncode == 2
    assert "BDX_MAIN_HOST is required" in result.stderr
    assert "BDX_MAIN_HOST=172.22.50.2" in result.stderr


def test_empty_runtime_env_host_fails(tmp_path: Path):
    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text("BDX_MAIN_HOST=\n", encoding="utf-8")

    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                "bdx_stack_parse_args overview",
                "bdx_stack_load_runtime_environment",
            ]
        ),
        env={"BDX_RUNTIME_ENV": str(runtime_env), "BDX_MAIN_HOST": ""},
        check=False,
    )

    assert result.returncode == 2
    assert "BDX_MAIN_HOST is required" in result.stderr


def test_loopback_runtime_host_requires_explicit_allow_loopback(tmp_path: Path):
    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text("BDX_MAIN_HOST=127.0.0.1\n", encoding="utf-8")

    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                "bdx_stack_parse_args overview",
                "bdx_stack_load_runtime_environment",
            ]
        ),
        env={"BDX_RUNTIME_ENV": str(runtime_env), "BDX_MAIN_HOST": ""},
        check=False,
    )

    assert result.returncode == 2
    assert "127.0.0.1 is not valid for operational use" in result.stderr


def test_allow_loopback_is_explicit(tmp_path: Path):
    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text("BDX_MAIN_HOST=127.0.0.1\n", encoding="utf-8")

    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                "bdx_stack_parse_args --allow-loopback overview",
                "bdx_stack_load_runtime_environment",
                'printf "%s\\n" "$BDX_MAIN_HOST"',
                'printf "%s\\n" "$BDX_MAIN_HOST_SOURCE"',
            ]
        ),
        env={"BDX_RUNTIME_ENV": str(runtime_env), "BDX_MAIN_HOST": ""},
    )

    assert result.stdout.splitlines() == ["127.0.0.1", "config/runtime.env"]


def test_stack_channel_access_environment_is_derived_from_main_host():
    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                "bdx_stack_parse_args --main-host 172.22.50.2 overview",
                "bdx_stack_load_runtime_environment",
                'printf "%s\\n" "$BDX_EPICS_INTERFACE"',
                'printf "%s\\n" "$EPICS_CA_ADDR_LIST"',
                'printf "%s\\n" "$EPICS_CA_AUTO_ADDR_LIST"',
                'printf "%s\\n" "$BDX_CA_ADDR_LIST"',
                'printf "%s\\n" "$BDX_CA_AUTO_ADDR_LIST"',
                'printf "%s\\n" "$BDX_MAIN_HOST_SOURCE"',
            ]
        ),
        env={"BDX_MAIN_HOST": ""},
    )

    assert result.stdout.splitlines() == [
        "172.22.50.2",
        "172.22.50.2 172.22.50.10",
        "NO",
        "172.22.50.2 172.22.50.10",
        "false",
        "command-line option",
    ]


def test_repository_archiver_scripts_take_precedence_over_installed_copies():
    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                'printf "%s\\n" "$ARCHIVER_STATUS"',
                'printf "%s\\n" "$ARCHIVER_HEALTHCHECK"',
                'printf "%s\\n" "$ARCHIVER_START"',
                'printf "%s\\n" "$ARCHIVER_AUTOREGISTER"',
                'printf "%s\\n" "$ARCHIVER_REPAIR"',
            ]
        )
    )

    paths = result.stdout.splitlines()
    assert all("/deploy/archiver-appliance/scripts/" in path for path in paths)
    assert all(".local/share/bdx-archiver/app/scripts" not in path for path in paths)


def test_chiller_archiver_readiness_probe_remains_run_state():
    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                'printf "%s\\n" "$ARCHIVER_CHILLER_READY_PV"',
            ]
        )
    )

    assert result.stdout.strip() == "BDX:CHILLER:CHILLER1:RUN_STATE"


def test_component_ready_urls_are_component_specific():
    result = _run_bash(
        "\n".join(
            [
                f'source "{ARCHIVER_COMMON}"',
                _archiver_url_exports(),
                "bdx_component_ready_url mgmt",
                "bdx_component_ready_url engine",
                "bdx_component_ready_url etl",
                "bdx_component_ready_url retrieval",
            ]
        )
    )

    assert result.stdout.splitlines() == [
        "http://127.0.0.1:17665/mgmt/bpl/getVersions",
        "http://127.0.0.1:17666/engine/bpl/getVersion",
        "http://127.0.0.1:17667/etl/bpl/getVersion",
        "http://127.0.0.1:17668/retrieval/bpl/getVersion",
    ]


def test_archiver_state_healthy_with_four_processes_and_component_ready_endpoints(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    status = tmp_path / "status.sh"
    env_file = tmp_path / "archappl.env"
    env_file.write_text("", encoding="utf-8")

    _write_fake_script(
        status,
        "\n".join(
            [
                'echo "mgmt: running pid 101"',
                'echo "engine: running pid 102"',
                'echo "etl: running pid 103"',
                'echo "retrieval: running pid 104"',
            ]
        ),
    )
    _write_fake_script(
        fake_bin / "curl",
        "\n".join(
            [
                'case "${*: -1}" in',
                '  *"/mgmt/bpl/getVersions"|*"/engine/bpl/getVersion"|*"/etl/bpl/getVersion"|*"/retrieval/bpl/getVersion") echo \'{"version":"test"}\'; exit 0 ;;',
                "  *) exit 1 ;;",
                "esac",
            ]
        ),
    )

    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                _archiver_url_exports(),
                f'ARCHIVER_STATUS="{status}"',
                f'ARCHIVER_ENV_FILE="{env_file}"',
                "bdx_stack_archiver_state",
            ]
        ),
        env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.stdout.strip() == "healthy"


def test_archiver_state_starting_when_all_processes_exist_but_endpoint_is_not_ready(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    status = tmp_path / "status.sh"
    env_file = tmp_path / "archappl.env"
    env_file.write_text("", encoding="utf-8")

    _write_fake_script(
        status,
        "\n".join(
            [
                'echo "mgmt: running pid 101"',
                'echo "engine: running pid 102"',
                'echo "etl: running pid 103"',
                'echo "retrieval: running pid 104"',
            ]
        ),
    )
    _write_fake_script(
        fake_bin / "curl",
        "\n".join(
            [
                'case "${*: -1}" in',
                '  *"/mgmt/bpl/getVersions"|*"/engine/bpl/getVersion"|*"/retrieval/bpl/getVersion") echo \'{"version":"test"}\'; exit 0 ;;',
                "  *) exit 1 ;;",
                "esac",
            ]
        ),
    )

    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                _archiver_url_exports(),
                f'ARCHIVER_STATUS="{status}"',
                f'ARCHIVER_ENV_FILE="{env_file}"',
                "bdx_stack_archiver_state",
                "bdx_stack_archiver_first_unready_endpoint",
            ]
        ),
        env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.stdout.splitlines() == [
        "starting",
        "etl endpoint is not ready: http://127.0.0.1:17667/etl/bpl/getVersion",
    ]


def test_archiver_state_partial_when_only_subset_of_processes_exists(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    status = tmp_path / "status.sh"
    env_file = tmp_path / "archappl.env"
    env_file.write_text("", encoding="utf-8")

    _write_fake_script(fake_bin / "curl", "exit 1")
    _write_fake_script(
        status,
        "\n".join(
            [
                'echo "mgmt: running pid 101"',
                'echo "engine: not running"',
                'echo "etl: not running"',
                'echo "retrieval: not running"',
                "exit 1",
            ]
        ),
    )

    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                _archiver_url_exports(),
                f'ARCHIVER_STATUS="{status}"',
                f'ARCHIVER_ENV_FILE="{env_file}"',
                "bdx_stack_archiver_state",
            ]
        ),
        env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.stdout.strip() == "partial"


def test_read_only_archiver_report_does_not_require_expert_environment(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_script(fake_bin / "curl", "exit 7")

    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                "unset BDX_ARCHIVER_MGMT_URL BDX_ARCHIVER_ENGINE_URL",
                "unset BDX_ARCHIVER_ETL_URL BDX_ARCHIVER_RETRIEVAL_BPL_URL",
                "bdx_stack_report_archiver_status",
            ]
        ),
        env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    assert "completely absent" in result.stdout


def test_archiver_state_is_healthy_with_version_endpoints(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    status = tmp_path / "status.sh"
    env_file = tmp_path / "archappl.env"
    curl_log = tmp_path / "curl.log"
    env_file.write_text("", encoding="utf-8")

    _write_fake_script(
        status,
        "\n".join(
            [
                'echo "mgmt: running pid 101"',
                'echo "engine: running pid 102"',
                'echo "etl: running pid 103"',
                'echo "retrieval: running pid 104"',
            ]
        ),
    )
    _write_fake_script(
        fake_bin / "curl",
        "\n".join(
            [
                'url="${*: -1}"',
                f'printf "%s\\n" "$url" >> "{curl_log}"',
                'case "$url" in',
                '  *"/mgmt/bpl/getVersions"|*"/engine/bpl/getVersion"|*"/etl/bpl/getVersion"|*"/retrieval/bpl/getVersion") echo \'{"version":"test"}\'; exit 0 ;;',
                '  *"/engine/bpl/getVersions"|*"/etl/bpl/getVersions"|*"/retrieval/bpl/getVersions") exit 22 ;;',
                "  *) exit 1 ;;",
                "esac",
            ]
        ),
    )

    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                _archiver_url_exports(),
                f'ARCHIVER_STATUS="{status}"',
                f'ARCHIVER_ENV_FILE="{env_file}"',
                "bdx_stack_archiver_state",
            ]
        ),
        env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    requested_urls = curl_log.read_text(encoding="utf-8")
    assert result.stdout.strip() == "healthy"
    assert "/mgmt/bpl/getVersions" in requested_urls
    assert "/engine/bpl/getVersion" in requested_urls
    assert "/etl/bpl/getVersion" in requested_urls
    assert "/retrieval/bpl/getVersion" in requested_urls
    assert "/engine/bpl/getVersions" not in requested_urls
    assert "/etl/bpl/getVersions" not in requested_urls
    assert "/retrieval/bpl/getVersions" not in requested_urls


def test_archiver_readiness_retries_temporary_http_failures(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_dir = tmp_path / "curl-state"
    state_dir.mkdir()
    status = tmp_path / "status.sh"
    env_file = tmp_path / "archappl.env"
    env_file.write_text("", encoding="utf-8")
    _write_fake_script(
        status,
        "\n".join(
            [
                'echo "mgmt: running pid 101"',
                'echo "engine: running pid 102"',
                'echo "etl: running pid 103"',
                'echo "retrieval: running pid 104"',
            ]
        ),
    )
    _write_fake_script(
        fake_bin / "curl",
        "\n".join(
            [
                'url="${*: -1}"',
                'case "$url" in',
                '  *"/mgmt/"*) component=mgmt ;;',
                '  *"/engine/"*) component=engine ;;',
                '  *"/etl/"*) component=etl ;;',
                '  *"/retrieval/"*) component=retrieval ;;',
                "  *) exit 22 ;;",
                "esac",
                f'counter="{state_dir}/$component"',
                'count="$(cat "$counter" 2>/dev/null || echo 0)"',
                'count=$((count + 1))',
                'printf "%s\\n" "$count" > "$counter"',
                "if [[ \"$count\" -eq 1 ]]; then exit 22; fi",
                "echo '{\"version\":\"test\"}'",
            ]
        ),
    )
    _write_fake_script(fake_bin / "systemctl", "exit 1")
    _write_fake_script(fake_bin / "sleep", "exit 0")

    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                _archiver_url_exports(),
                f'ARCHIVER_STATUS="{status}"',
                f'ARCHIVER_ENV_FILE="{env_file}"',
                "bdx_stack_wait_for_archiver_healthy 5",
            ]
        ),
        env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.returncode == 0
    assert all(
        int((state_dir / component).read_text(encoding="utf-8")) >= 2
        for component in ("mgmt", "engine", "etl", "retrieval")
    )


def test_archiver_readiness_permanent_failure_is_bounded(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    status = tmp_path / "status.sh"
    env_file = tmp_path / "archappl.env"
    env_file.write_text("", encoding="utf-8")
    _write_fake_script(
        status,
        "\n".join(
            [
                'echo "mgmt: running pid 101"',
                'echo "engine: running pid 102"',
                'echo "etl: running pid 103"',
                'echo "retrieval: running pid 104"',
            ]
        ),
    )
    _write_fake_script(fake_bin / "curl", "exit 22")
    _write_fake_script(fake_bin / "systemctl", "exit 1")

    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                _archiver_url_exports(),
                f'ARCHIVER_STATUS="{status}"',
                f'ARCHIVER_ENV_FILE="{env_file}"',
                "bdx_stack_wait_for_archiver_healthy 0",
            ]
        ),
        env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        check=False,
    )

    assert result.returncode != 0
    assert "Timed out waiting for Archiver Appliance health" in result.stderr


def test_archiver_readiness_detects_component_exit_during_startup(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "status-calls"
    status = tmp_path / "status.sh"
    env_file = tmp_path / "archappl.env"
    env_file.write_text("", encoding="utf-8")
    _write_fake_script(
        status,
        "\n".join(
            [
                f'calls="$(cat "{calls}" 2>/dev/null || echo 0)"',
                "calls=$((calls + 1))",
                f'printf "%s\\n" "$calls" > "{calls}"',
                'echo "mgmt: running pid 101"',
                'echo "engine: running pid 102"',
                'echo "etl: running pid 103"',
                'if [[ "$calls" -lt 3 ]]; then echo "retrieval: running pid 104"; else echo "retrieval: not running"; fi',
            ]
        ),
    )
    _write_fake_script(fake_bin / "curl", "exit 22")
    _write_fake_script(fake_bin / "systemctl", "exit 1")
    _write_fake_script(fake_bin / "sleep", "exit 0")

    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                _archiver_url_exports(),
                f'ARCHIVER_STATUS="{status}"',
                f'ARCHIVER_ENV_FILE="{env_file}"',
                "bdx_stack_wait_for_archiver_healthy 5",
            ]
        ),
        env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        check=False,
    )

    assert result.returncode != 0
    assert "An Archiver component exited during startup" in result.stderr


def test_stack_does_not_start_duplicate_ioc_when_requested_address_is_listening(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_script(fake_bin / "pgrep", "exit 1")
    _write_fake_script(
        fake_bin / "osascript",
        f'echo called > "{tmp_path / "osascript.log"}"',
    )

    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                "BDX_MAIN_HOST=172.22.50.2",
                'bdx_stack_main_ioc_port_listening() { [[ "$BDX_MAIN_HOST" == "172.22.50.2" ]]; }',
                "bdx_stack_start_ioc_if_needed",
            ]
        ),
        env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    assert "already listening on 172.22.50.2:5064" in result.stdout
    assert not (tmp_path / "osascript.log").exists()


def test_stack_rejects_existing_ioc_process_not_listening_on_requested_address(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_script(fake_bin / "pgrep", "exit 0")
    _write_fake_script(
        fake_bin / "osascript",
        f'echo called > "{tmp_path / "osascript.log"}"',
    )

    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                "BDX_MAIN_HOST=172.22.50.2",
                "bdx_stack_main_ioc_port_listening() { return 1; }",
                "bdx_stack_start_ioc_if_needed",
            ]
        ),
        env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        check=False,
    )

    assert result.returncode != 0
    assert "not listening on 172.22.50.2:5064" in result.stderr
    assert not (tmp_path / "osascript.log").exists()


def test_stack_ioc_terminal_command_records_actual_ioc_pid(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    osascript_log = tmp_path / "osascript.log"
    runtime = tmp_path / "runtime"
    _write_fake_script(fake_bin / "pgrep", "exit 1")
    _write_fake_script(
        fake_bin / "osascript",
        f'printf "%s\\n" "$*" > "{osascript_log}"',
    )

    _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                "BDX_MAIN_HOST=172.22.50.2",
                "BDX_EPICS_INTERFACE=172.22.50.2",
                f'BDX_STACK_RUNTIME_DIR="{runtime}"',
                f'IOC_PID_FILE="{runtime / "ioc.pid"}"',
                "bdx_stack_main_ioc_port_listening() { return 1; }",
                "bdx_stack_wait_for_ioc_listener() { return 0; }",
                "bdx_stack_start_ioc_if_needed",
            ]
        ),
        env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )

    command = osascript_log.read_text(encoding="utf-8")
    assert "ioc.pid" in command
    assert "printf" in command
    assert "$$" in command
    assert "exec " in command
    assert "bdx-prototype-ioc" in command


def test_stack_launch_forwards_selected_display_and_archiver_environment(tmp_path: Path):
    fake_launcher = tmp_path / "launch_phoebus.sh"
    _write_fake_script(
        fake_launcher,
        "\n".join(
            [
                f'printf "%s\\n" "$1" > "{tmp_path / "display.txt"}"',
                f'printf "%s\\n" "$BDX_ARCHIVER_ENABLED" > "{tmp_path / "archiver_enabled.txt"}"',
                f'printf "%s\\n" "$BDX_ARCHIVER_URL" > "{tmp_path / "archiver_url.txt"}"',
                f'printf "%s\\n" "$BDX_ARCHIVER_PREFLIGHT_PV" > "{tmp_path / "preflight_pv.txt"}"',
            ]
        ),
    )

    _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                f'PHOEBUS_LAUNCHER="{fake_launcher}"',
                "bdx_stack_launch_phoebus chiller",
            ]
        )
    )

    assert (tmp_path / "display.txt").read_text(encoding="utf-8").strip() == "chiller"
    assert (tmp_path / "archiver_enabled.txt").read_text(encoding="utf-8").strip() == "true"
    assert (
        (tmp_path / "archiver_url.txt").read_text(encoding="utf-8").strip()
        == "http://127.0.0.1:17668/retrieval"
    )
    assert (tmp_path / "preflight_pv.txt").read_text(encoding="utf-8").strip() == ""


def test_stack_main_launches_phoebus_when_archiver_is_absent(tmp_path: Path):
    trace = tmp_path / "trace.log"
    result = _run_bash(
        "\n".join(
            [
                f'source "{START_SCRIPT}"',
                f'TRACE="{trace}"',
                'record() { printf "%s\\n" "$1" >> "$TRACE"; }',
                "bdx_stack_parse_args() { BDX_STACK_DISPLAY=overview; record parse; }",
                "bdx_stack_load_runtime_environment() { record environment; }",
                "bdx_stack_validate_slow_control_installation() { record validate; }",
                "bdx_stack_print_summary() { record summary; }",
                "bdx_stack_start_ioc_if_needed() { record start-ioc; }",
                "bdx_stack_wait_for_ioc_listener() { record listener; }",
                "bdx_stack_wait_for_pv_read() { record ready-pv; }",
                "bdx_stack_report_archiver_status() { record archiver-absent; }",
                "bdx_stack_launch_phoebus() { record phoebus; }",
                "bdx_stack_main overview",
            ]
        ),
        check=False,
    )

    assert result.returncode == 0
    assert trace.read_text(encoding="utf-8").splitlines() == [
        "parse",
        "environment",
        "validate",
        "summary",
        "start-ioc",
        "listener",
        "ready-pv",
        "archiver-absent",
        "phoebus",
    ]


def test_phoebus_direct_launch_records_pid_and_mode(tmp_path: Path):
    fake_phoebus = tmp_path / "phoebus.sh"
    runtime = tmp_path / "runtime"
    _write_fake_script(fake_phoebus, "exit 0")

    subprocess.run(
        ["bash", str(PHOEBUS_SCRIPT), "overview"],
        check=True,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "BDX_MAIN_HOST": "172.22.50.2",
            "BDX_PHOEBUS_CMD": str(fake_phoebus),
            "BDX_PHOEBUS_ENV": str(tmp_path / "missing.env"),
            "BDX_STACK_RUNTIME_DIR": str(runtime),
            "XDG_RUNTIME_DIR": str(tmp_path),
        },
    )

    assert (runtime / "phoebus.mode").read_text(encoding="utf-8").strip() == "direct"
    recorded_pid = (runtime / "phoebus.pid").read_text(encoding="utf-8").strip()
    assert recorded_pid.isdigit()


def test_phoebus_archiver_preflight_uses_iso_timestamps_and_url_encoding(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl_log = tmp_path / "curl-url.txt"
    fake_phoebus = tmp_path / "phoebus.sh"

    _write_fake_script(
        fake_bin / "curl",
        "\n".join(
            [
                'last_arg="${*: -1}"',
                f'printf "%s\\n" "$last_arg" > "{curl_log}"',
                "exit 1",
            ]
        ),
    )
    _write_fake_script(fake_phoebus, "exit 0")

    env = {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "BDX_MAIN_HOST": "172.22.50.2",
        "BDX_PHOEBUS_CMD": str(fake_phoebus),
        "BDX_PHOEBUS_ENV": str(tmp_path / "missing.env"),
        "BDX_ARCHIVER_ENABLED": "true",
        "BDX_ARCHIVER_URL": "http://127.0.0.1:17668/retrieval",
        "BDX_ARCHIVER_STRICT_CHECK": "false",
        "BDX_ARCHIVER_PREFLIGHT_PV": "BDX:ENV:TEMP:T00:VALUE",
        "XDG_RUNTIME_DIR": str(tmp_path),
    }

    result = subprocess.run(
        ["bash", str(PHOEBUS_SCRIPT), "overview"],
        check=True,
        text=True,
        capture_output=True,
        env={**os.environ, **env},
    )

    url = curl_log.read_text(encoding="utf-8").strip()
    assert "pv=BDX%3AENV%3ATEMP%3AT00%3AVALUE" in url
    assert "from=-" not in url
    assert "to=now" not in url
    assert re.search(r"from=\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}Z", url)
    assert re.search(r"to=\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}Z", url)
    assert "normal live control" in result.stderr
    assert "Historical data is unavailable" in result.stderr
