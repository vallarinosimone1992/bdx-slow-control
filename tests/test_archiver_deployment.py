import hashlib
import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

from bdx_slow_control.builders import build_chiller, build_environment, build_psu
from bdx_slow_control.config import load_json


ARCHIVER = Path("deploy/archiver-appliance")
SCRIPTS = ARCHIVER / "scripts"
PV_LISTS = ARCHIVER / "pv-lists"


def _read_pvs(path: Path) -> list[str]:
    pvs = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            pvs.append(line)
    return pvs


def _load_python_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_minimal_archiver_env(tmp_path: Path) -> Path:
    env_file = tmp_path / "archappl.env"
    env_file.write_text(
        "\n".join(
            [
                "BDX_ARCHIVER_RELEASE_VERSION=2.3.1",
                "BDX_ARCHIVER_RELEASE_ARTIFACT=archappl_v2.3.1.tar.gz",
                "BDX_ARCHIVER_RELEASE_URL=https://example.invalid/archappl_v2.3.1.tar.gz",
                "BDX_ARCHIVER_RELEASE_SHA256=ce2eabe57915a99bc9be22d29d400f112f63931b5d5af9394e8504702d16722f",
                f"BDX_ARCHIVER_APP_DIR={tmp_path / 'app'}",
                f"BDX_ARCHIVER_CONFIG_DIR={tmp_path / 'config'}",
                f"BDX_ARCHIVER_STATE_DIR={tmp_path / 'state'}",
                f"BDX_ARCHIVER_LOG_DIR={tmp_path / 'logs'}",
                f"BDX_ARCHIVER_CACHE_DIR={tmp_path / 'cache'}",
                f"BDX_ARCHIVER_TMP_DIR={tmp_path / 'state' / 'tmp'}",
                f"BDX_ARCHIVER_SHORT_TERM_DIR={tmp_path / 'state' / 'sts'}",
                f"BDX_ARCHIVER_MEDIUM_TERM_DIR={tmp_path / 'state' / 'mts'}",
                f"BDX_ARCHIVER_LONG_TERM_DIR={tmp_path / 'state' / 'lts'}",
                f"BDX_ARCHIVER_PERSISTENCE_DIR={tmp_path / 'state' / 'persistence'}",
                f"BDX_ARCHIVER_TOMCAT_HOME={tmp_path / 'tomcat'}",
                f"BDX_ARCHIVER_WAR_DIR={tmp_path / 'war'}",
                f"BDX_ARCHIVER_TOMCAT_BASE_DIR={tmp_path / 'tomcat-bases'}",
                "BDX_ARCHIVER_TOMCAT_TARBALL=",
                "BDX_ARCHIVER_APPLIANCE_ID=test0",
                "BDX_ARCHIVER_CLUSTER_HOST=127.0.0.1",
                "BDX_ARCHIVER_CLUSTER_PORT=16670",
                "BDX_ARCHIVER_MGMT_URL=http://127.0.0.1:17665/mgmt/bpl",
                "BDX_ARCHIVER_ENGINE_URL=http://127.0.0.1:17666/engine/bpl",
                "BDX_ARCHIVER_ETL_URL=http://127.0.0.1:17667/etl/bpl",
                "BDX_ARCHIVER_RETRIEVAL_BPL_URL=http://127.0.0.1:17668/retrieval/bpl",
                "BDX_ARCHIVER_DATA_RETRIEVAL_URL=http://127.0.0.1:17668/retrieval",
                "BDX_ARCHIVER_MEDIUM_TERM_HOLD_DAYS=60",
                "BDX_ARCHIVER_AUTO_REGISTER=true",
                "BDX_ARCHIVER_PV_LISTS=",
                "BDX_ARCHIVER_REGISTER_RETRY_SECONDS=30",
                "ARCHAPPL_PERSISTENCE_LAYER=org.example.PersistentLayer",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return env_file


def test_archiver_deployment_tree_exists():
    expected = {
        "README.md",
        "VERSION",
        "CHECKSUMS",
        ".gitignore",
        "config/archappl.env.example",
        "config/appliances.xml",
        "config/policies.py",
        "config/persistence.example",
        "pv-lists/environment.txt",
        "pv-lists/psu.txt",
        "pv-lists/chiller.txt",
        "pv-lists/prototype.txt",
        "scripts/install.sh",
        "scripts/configure.sh",
        "scripts/start.sh",
        "scripts/stop.sh",
        "scripts/status.sh",
        "scripts/healthcheck.sh",
        "scripts/archiver_common.py",
        "scripts/auto-register-pvs.sh",
        "scripts/register-pvs.py",
        "scripts/repair_archiver.py",
        "scripts/repair-archiver.sh",
        "scripts/test-archive-batches.py",
        "scripts/verify-retrieval.py",
        "scripts/backup-config.sh",
        "scripts/uninstall.sh",
        "systemd/bdx-archiver.service",
        "systemd/bdx-archiver-user.service",
    }
    assert {str(path.relative_to(ARCHIVER)) for path in ARCHIVER.rglob("*") if path.is_file()} >= expected


def test_archiver_release_is_pinned_with_checksum():
    version_text = (ARCHIVER / "VERSION").read_text(encoding="utf-8")
    checksum_text = (ARCHIVER / "CHECKSUMS").read_text(encoding="utf-8")

    assert "ARCHIVER_APPLIANCE_VERSION=2.3.1" in version_text
    assert "archappl_v2.3.1.tar.gz" in version_text
    assert "JAVA_REQUIRED_MAJOR=21" in version_text
    assert "TOMCAT_SUPPORTED_MAJOR=11" in version_text
    assert "ce2eabe57915a99bc9be22d29d400f112f63931b5d5af9394e8504702d16722f" in checksum_text


def test_archiver_scripts_have_valid_shell_syntax():
    for script in sorted(SCRIPTS.glob("*.sh")):
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_archiver_operator_scripts_are_executable():
    for script in [
        "install.sh",
        "configure.sh",
        "start.sh",
        "stop.sh",
        "status.sh",
        "healthcheck.sh",
        "auto-register-pvs.sh",
        "register-pvs.py",
        "repair_archiver.py",
        "repair-archiver.sh",
        "test-archive-batches.py",
        "verify-retrieval.py",
        "backup-config.sh",
        "uninstall.sh",
    ]:
        mode = (SCRIPTS / script).stat().st_mode
        assert mode & stat.S_IXUSR


def test_low_level_start_has_fixed_order_and_never_registers_catalog():
    text = (SCRIPTS / "start.sh").read_text(encoding="utf-8")

    assert "printf \"%s\\n\" mgmt engine etl retrieval" in (
        SCRIPTS / "common.sh"
    ).read_text(encoding="utf-8")
    assert "bdx_archiver_start_registration_retry" not in text
    assert "Automatic catalog registration is disabled during component startup" in text


def test_user_service_owns_persistent_foreground_lifecycle():
    text = (ARCHIVER / "systemd/bdx-archiver-user.service").read_text(
        encoding="utf-8"
    )

    assert "--foreground --user-local" in text
    assert "ExecStop=" in text
    assert "scripts}/stop.sh" in text
    assert "Environment=BDX_ARCHIVER_AUTO_REGISTER=false" in text
    assert "KillMode=control-group" in text


def test_catalog_repair_uses_a_non_overlapping_process_lock():
    text = (SCRIPTS / "repair-archiver.sh").read_text(encoding="utf-8")

    assert "repair-archiver.lock" in text
    assert "flock -n 9" in text
    assert "Another Archiver catalog repair is already active" in text
    assert "PYTHONUNBUFFERED=1" in text


def test_start_and_stop_reconcile_stale_pids_without_duplicates(tmp_path: Path):
    env_file = _write_minimal_archiver_env(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    fake_curl.chmod(0o755)

    tomcat_home = tmp_path / "tomcat"
    catalina = tomcat_home / "bin" / "catalina.sh"
    catalina.parent.mkdir(parents=True)
    stop_log = tmp_path / "stop.log"
    catalina.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'case "${1:-}" in',
                "  start)",
                "    bash -c 'exec -a \"$1\" sleep 30' _ \"java -Dcatalina.base=$CATALINA_BASE\" </dev/null >/dev/null 2>&1 &",
                '    printf "%s\\n" "$!" > "$CATALINA_PID"',
                "    ;;&",
                "  stop)",
                '    basename "$CATALINA_BASE" >> "$FAKE_STOP_LOG"',
                '    pid="$(cat "$CATALINA_PID" 2>/dev/null || true)"',
                '    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true',
                "    ;;&",
                "esac",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    catalina.chmod(0o755)
    bases = tmp_path / "tomcat-bases"
    for component in ("mgmt", "engine", "etl", "retrieval"):
        (bases / component).mkdir(parents=True)
    stale = bases / "mgmt" / "tomcat.pid"
    stale.write_text(f"{os.getpid()}\n", encoding="utf-8")

    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_STOP_LOG": str(stop_log),
    }
    started_pids: list[int] = []
    try:
        first = subprocess.run(
            [str(SCRIPTS / "start.sh"), "--env", str(env_file)],
            check=True,
            text=True,
            capture_output=True,
            env=environment,
        )
        started_pids = [
            int((bases / component / "tomcat.pid").read_text(encoding="utf-8"))
            for component in ("mgmt", "engine", "etl", "retrieval")
        ]
        assert len(set(started_pids)) == 4
        assert "Removing stale mgmt PID file" in first.stderr

        second = subprocess.run(
            [str(SCRIPTS / "start.sh"), "--env", str(env_file)],
            check=True,
            text=True,
            capture_output=True,
            env=environment,
        )
        repeated_pids = [
            int((bases / component / "tomcat.pid").read_text(encoding="utf-8"))
            for component in ("mgmt", "engine", "etl", "retrieval")
        ]
        assert repeated_pids == started_pids
        assert second.stdout.count("is already running") == 4

        subprocess.run(
            [str(SCRIPTS / "stop.sh"), "--env", str(env_file)],
            check=True,
            text=True,
            capture_output=True,
            env=environment,
        )
        assert stop_log.read_text(encoding="utf-8").splitlines() == [
            "retrieval",
            "etl",
            "engine",
            "mgmt",
        ]

        repeated_stop = subprocess.run(
            [str(SCRIPTS / "stop.sh"), "--env", str(env_file)],
            check=True,
            text=True,
            capture_output=True,
            env=environment,
        )
        assert repeated_stop.stdout.count("is already stopped") == 4
    finally:
        for pid in started_pids:
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass


def test_archiver_env_path_generation_is_configurable(tmp_path: Path):
    env_file = tmp_path / "archappl.env"
    env_file.write_text(
        "\n".join(
            [
                "BDX_ARCHIVER_RELEASE_VERSION=2.3.1",
                "BDX_ARCHIVER_RELEASE_ARTIFACT=archappl_v2.3.1.tar.gz",
                "BDX_ARCHIVER_RELEASE_URL=https://example.invalid/archappl_v2.3.1.tar.gz",
                "BDX_ARCHIVER_RELEASE_SHA256=ce2eabe57915a99bc9be22d29d400f112f63931b5d5af9394e8504702d16722f",
                f"BDX_ARCHIVER_APP_DIR={tmp_path / 'app'}",
                f"BDX_ARCHIVER_CONFIG_DIR={tmp_path / 'config'}",
                f"BDX_ARCHIVER_STATE_DIR={tmp_path / 'state'}",
                f"BDX_ARCHIVER_LOG_DIR={tmp_path / 'logs'}",
                f"BDX_ARCHIVER_CACHE_DIR={tmp_path / 'cache'}",
                f"BDX_ARCHIVER_TMP_DIR={tmp_path / 'state' / 'tmp'}",
                f"BDX_ARCHIVER_SHORT_TERM_DIR={tmp_path / 'state' / 'sts'}",
                f"BDX_ARCHIVER_MEDIUM_TERM_DIR={tmp_path / 'state' / 'mts'}",
                f"BDX_ARCHIVER_LONG_TERM_DIR={tmp_path / 'state' / 'lts'}",
                f"BDX_ARCHIVER_PERSISTENCE_DIR={tmp_path / 'state' / 'persistence'}",
                f"BDX_ARCHIVER_TOMCAT_HOME={tmp_path / 'tomcat'}",
                f"BDX_ARCHIVER_WAR_DIR={tmp_path / 'war'}",
                f"BDX_ARCHIVER_TOMCAT_BASE_DIR={tmp_path / 'tomcat-bases'}",
                "BDX_ARCHIVER_TOMCAT_TARBALL=",
                "BDX_ARCHIVER_APPLIANCE_ID=test0",
                "BDX_ARCHIVER_CLUSTER_HOST=127.0.0.1",
                "BDX_ARCHIVER_CLUSTER_PORT=16670",
                "BDX_ARCHIVER_MGMT_URL=http://127.0.0.1:17665/mgmt/bpl",
                "BDX_ARCHIVER_ENGINE_URL=http://127.0.0.1:17666/engine/bpl",
                "BDX_ARCHIVER_ETL_URL=http://127.0.0.1:17667/etl/bpl",
                "BDX_ARCHIVER_RETRIEVAL_BPL_URL=http://127.0.0.1:17668/retrieval/bpl",
                "BDX_ARCHIVER_DATA_RETRIEVAL_URL=http://127.0.0.1:17668/retrieval",
                "BDX_ARCHIVER_MEDIUM_TERM_HOLD_DAYS=60",
                "ARCHAPPL_PERSISTENCE_LAYER=org.example.PersistentLayer",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(SCRIPTS / "configure.sh"), "--env", str(env_file), "--print-paths"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert f"ARCHAPPL_APPLIANCES={tmp_path / 'config' / 'appliances.xml'}" in result.stdout
    assert f"ARCHAPPL_SHORT_TERM_FOLDER={tmp_path / 'state' / 'sts'}" in result.stdout


def test_archiver_auto_registration_command_defaults_to_deployed_lists(
    tmp_path: Path,
):
    env_file = _write_minimal_archiver_env(tmp_path)

    result = subprocess.run(
        [
            str(SCRIPTS / "auto-register-pvs.sh"),
            "--env",
            str(env_file),
            "--print-command",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "repair_archiver.py" in result.stdout
    assert "--mgmt-url http://127.0.0.1:17665/mgmt/bpl" in result.stdout
    assert f"{tmp_path / 'app' / 'pv-lists' / 'psu.txt'}" in result.stdout
    assert f"{tmp_path / 'app' / 'pv-lists' / 'chiller.txt'}" in result.stdout
    assert f"{tmp_path / 'app' / 'pv-lists' / 'environment.txt'}" in result.stdout


def test_archiver_auto_registration_explicit_pv_lists_override_defaults(tmp_path: Path):
    env_file = _write_minimal_archiver_env(tmp_path)
    override_a = tmp_path / "custom-a.txt"
    override_b = tmp_path / "custom-b.txt"
    override_a.write_text("BDX:TEST:A\n", encoding="utf-8")
    override_b.write_text("BDX:TEST:B\n", encoding="utf-8")
    with env_file.open("a", encoding="utf-8") as stream:
        stream.write(f'BDX_ARCHIVER_PV_LISTS="{override_a} {override_b}"\n')

    result = subprocess.run(
        [
            str(SCRIPTS / "auto-register-pvs.sh"),
            "--env",
            str(env_file),
            "--print-command",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert str(override_a) in result.stdout
    assert str(override_b) in result.stdout
    assert f"{tmp_path / 'app' / 'pv-lists' / 'psu.txt'}" not in result.stdout
    assert f"{tmp_path / 'app' / 'pv-lists' / 'chiller.txt'}" not in result.stdout
    assert f"{tmp_path / 'app' / 'pv-lists' / 'environment.txt'}" not in result.stdout


def test_archiver_auto_registration_duplicate_start_is_ignored(tmp_path: Path):
    env_file = _write_minimal_archiver_env(tmp_path)
    pid_file = tmp_path / "state" / "run" / "auto-register-pvs.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")

    script = "\n".join(
        [
            "set -euo pipefail",
            f'SCRIPT_DIR="{SCRIPTS.resolve()}"',
            'source "$SCRIPT_DIR/common.sh"',
            f'bdx_load_env "{env_file}"',
            "bdx_export_archappl_env",
            f'bdx_archiver_start_registration_retry "{env_file}" 0',
        ]
    )
    result = subprocess.run(
        ["bash", "-c", script],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "already running" in result.stdout
    assert pid_file.read_text(encoding="utf-8") == f"{os.getpid()}\n"


def test_archiver_readiness_checks_use_functional_bpl_endpoints(tmp_path: Path):
    env_file = _write_minimal_archiver_env(tmp_path)
    script = "\n".join(
        [
            "set -euo pipefail",
            f'SCRIPT_DIR="{SCRIPTS.resolve()}"',
            'source "$SCRIPT_DIR/common.sh"',
            f'bdx_load_env "{env_file}"',
            "for component in mgmt engine etl retrieval; do",
            '    bdx_component_ready_url "$component"',
            "done",
        ]
    )

    result = subprocess.run(
        ["bash", "-c", script],
        check=True,
        text=True,
        capture_output=True,
    )

    urls = result.stdout.splitlines()
    assert urls == [
        "http://127.0.0.1:17665/mgmt/bpl/getVersions",
        "http://127.0.0.1:17666/engine/bpl/getVersion",
        "http://127.0.0.1:17667/etl/bpl/getVersion",
        "http://127.0.0.1:17668/retrieval/bpl/getVersion",
    ]
    assert all(not url.endswith("/bpl") for url in urls)


def test_archiver_checksum_verification_uses_sha256(tmp_path: Path):
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"bdx archiver checksum test\n")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()

    subprocess.run(
        [str(SCRIPTS / "install.sh"), "--verify-checksum", str(payload), digest],
        check=True,
        text=True,
        capture_output=True,
    )


def test_archiver_pv_lists_match_current_profiles():
    env_pvs = set(_read_pvs(PV_LISTS / "environment.txt"))
    psu_pvs = set(_read_pvs(PV_LISTS / "psu.txt"))
    chiller_pvs = set(_read_pvs(PV_LISTS / "chiller.txt"))

    env_db, _ = build_environment(load_json(Path("config/profiles/raspberry/environment.json")))
    psu_db, _ = build_psu(load_json(Path("config/profiles/main-server/psu.json")))
    chiller_db, _ = build_chiller(load_json(Path("config/profiles/main-server/chiller.json")))

    assert env_pvs.issubset(env_db)
    assert psu_pvs.issubset(psu_db)
    assert chiller_pvs.issubset(chiller_db)


def test_archiver_pv_lists_exclude_command_and_staging_pvs():
    disallowed_suffixes = (
        ":ALLOFF_CMD",
        ":APPLY_MESSAGE",
        ":APPLY_STATUS",
        ":CLEAR_ERROR_CMD",
        ":COMM_TIMEOUT_SET",
        ":APPLY_CMD",
        ":APPLY_SETPOINT_CMD",
        ":OUTPUT_SET",
        ":RUN_SET",
        ":SAFE_SETPOINT_SET",
        ":VOLTAGE_SET",
        ":CURRENT_LIMIT_SET",
        ":OVP_SET",
        ":OCP_SET",
        ":SETPOINT_SET",
        ":HEARTBEAT",
        ":VOLTAGE_REQUEST",
        ":CURRENT_LIMIT_REQUEST",
        ":SETPOINT_REQUEST",
    )
    for path in PV_LISTS.glob("*.txt"):
        for pv in _read_pvs(path):
            assert not pv.endswith(disallowed_suffixes), f"{path} archives command PV {pv}"


def test_archiver_configured_catalog_is_exactly_18_unique_essential_pvs():
    environment_pvs = _read_pvs(PV_LISTS / "environment.txt")
    psu_pvs = _read_pvs(PV_LISTS / "psu.txt")
    chiller_pvs = _read_pvs(PV_LISTS / "chiller.txt")
    configured = environment_pvs + psu_pvs + chiller_pvs

    expected_psu = [
        f"BDX:PSU:{device}:{channel}:{suffix}"
        for device in ("LV1", "LV2")
        for channel in ("CH1", "CH2")
        for suffix in ("VOLTAGE_SET_RBV", "VOLTAGE_RBV", "CURRENT_RBV")
    ]
    expected_chiller = [
        "BDX:CHILLER:CHILLER1:SETPOINT_RBV",
        "BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV",
    ]
    expected_environment = [
        f"BDX:ENV:TEMP:T{index:02d}:VALUE" for index in range(4)
    ]

    assert psu_pvs == expected_psu
    assert chiller_pvs == expected_chiller
    assert environment_pvs == expected_environment
    assert len(configured) == 18
    assert len(set(configured)) == 18
    assert not any(pv.endswith(":LAST_UPDATE") for pv in configured)


def test_archiver_chiller_list_excludes_disabled_optional_measurements():
    chiller_pvs = _read_pvs(PV_LISTS / "chiller.txt")

    assert "BDX:CHILLER:CHILLER1:PRESSURE_RBV" not in chiller_pvs
    assert "BDX:CHILLER:CHILLER1:EXTERNAL_TEMPERATURE_RBV" not in chiller_pvs
    assert "BDX:CHILLER:CHILLER1:PRESSURE_VALID" not in chiller_pvs
    assert "BDX:CHILLER:CHILLER1:EXTERNAL_TEMPERATURE_VALID" not in chiller_pvs


def test_archiver_chiller_list_excludes_controlled_and_diagnostic_pvs():
    chiller_pvs = set(_read_pvs(PV_LISTS / "chiller.txt"))

    assert "BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV" in chiller_pvs
    assert "BDX:CHILLER:CHILLER1:CONTROLLED_TEMPERATURE_RBV" not in chiller_pvs
    assert "BDX:CHILLER:CHILLER1:LAST_UPDATE" not in chiller_pvs
    assert "BDX:CHILLER:CHILLER1:ERROR_MESSAGE" not in chiller_pvs


def test_archiver_prototype_list_is_deduplicated_union_of_enabled_lists():
    environment_pvs = _read_pvs(PV_LISTS / "environment.txt")
    psu_pvs = _read_pvs(PV_LISTS / "psu.txt")
    chiller_pvs = _read_pvs(PV_LISTS / "chiller.txt")
    prototype_pvs = _read_pvs(PV_LISTS / "prototype.txt")

    union = list(dict.fromkeys(environment_pvs + psu_pvs + chiller_pvs))
    assert prototype_pvs == union
    assert len(prototype_pvs) == len(set(prototype_pvs))


def test_archiver_register_dry_run_reports_each_pv(tmp_path: Path):
    pv_file = tmp_path / "pvs.txt"
    pv_file.write_text(
        "\n".join(
            [
                "BDX:ENV:TEMP:T00:VALUE",
                "BDX:ENV:TEMP:T00:STATUS_OK",
                "BDX:PSU:LV1:CH1:ERROR_MESSAGE",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "register-pvs.py"), "--dry-run", str(pv_file)],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "DRY-RUN register BDX:ENV:TEMP:T00:VALUE policy=BDX_Physical_5s" in result.stdout
    assert "DRY-RUN register BDX:ENV:TEMP:T00:STATUS_OK policy=BDX_State_Change" in result.stdout
    assert "DRY-RUN register BDX:PSU:LV1:CH1:ERROR_MESSAGE policy=BDX_Diagnostic_Change" in result.stdout


def test_archiver_register_rejects_unarchivable_pvs(tmp_path: Path):
    pv_file = tmp_path / "pvs.txt"
    pv_file.write_text(
        "\n".join(
            [
                "BDX:ENV:HEARTBEAT",
                "BDX:PSU:LV1:CH1:OUTPUT_SET",
                "BDX:PSU:LV1:CH1:VOLTAGE_REQUEST",
                "BDX:CHILLER:CHILLER1:APPLY_SETPOINT_CMD",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "register-pvs.py"), "--dry-run", str(pv_file)],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "rejected BDX:ENV:HEARTBEAT" in result.stderr
    assert "rejected BDX:PSU:LV1:CH1:OUTPUT_SET" in result.stderr


def test_archiver_verify_retrieval_fixtures(tmp_path: Path):
    ok_fixture = tmp_path / "ok.json"
    ok_fixture.write_text(
        '[{"meta": {"name": "BDX:ENV:TEMP:T00:VALUE"}, "data": [{"secs": 1, "val": 21.5}]}]',
        encoding="utf-8",
    )
    no_samples_fixture = tmp_path / "no_samples.json"
    no_samples_fixture.write_text(
        '[{"meta": {"name": "BDX:ENV:TEMP:T00:VALUE"}, "data": []}]',
        encoding="utf-8",
    )
    unknown_fixture = tmp_path / "unknown.json"
    unknown_fixture.write_text('{"error": "PV not found"}', encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "verify-retrieval.py"),
            "--fixture",
            str(ok_fixture),
            "--pv",
            "BDX:ENV:TEMP:T00:VALUE",
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "verify-retrieval.py"),
            "--fixture",
            str(no_samples_fixture),
            "--pv",
            "BDX:ENV:TEMP:T00:VALUE",
            "--allow-no-samples",
        ],
        check=True,
    )
    failed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "verify-retrieval.py"),
            "--fixture",
            str(unknown_fixture),
            "--pv",
            "BDX:ENV:TEMP:T00:VALUE",
        ],
        check=False,
    )
    assert failed.returncode != 0


def test_archiver_tree_tracks_no_runtime_data_or_binaries():
    forbidden_suffixes = {
        ".war",
        ".jar",
        ".tar.gz",
        ".zip",
        ".log",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".pb",
        ".pbraw",
        ".parquet",
    }
    for path in ARCHIVER.rglob("*"):
        if path.is_file():
            assert not any(str(path).endswith(suffix) for suffix in forbidden_suffixes)


def test_archiver_tree_tracks_no_obvious_secrets():
    secret_markers = (
        "BEGIN PRIVATE KEY",
        "AWS_SECRET_ACCESS_KEY",
        "JDBC_PASSWORD=",
        "BDX_ARCHIVER_DATABASE_PASSWORD=",
    )
    for path in ARCHIVER.rglob("*"):
        if path.is_file() and path.stat().st_size < 200_000:
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not any(marker in text for marker in secret_markers), path


def test_archiver_policy_file_uses_official_policy_hooks():
    text = (ARCHIVER / "config" / "policies.py").read_text(encoding="utf-8")

    assert "def getPolicyList():" in text
    assert "def getFieldsArchivedAsPartOfStream():" in text
    assert "def determinePolicy(pvInfoDict):" in text
    assert "BDX_Physical_5s" in text
    assert "BDX_State_Change" in text
    assert "BDX_Diagnostic_Change" in text
    assert "BDX_Heartbeat_Slow" not in text


def test_archiver_policies_use_approved_sampling_and_storage(monkeypatch):
    monkeypatch.setenv("BDX_ARCHIVER_MEDIUM_TERM_HOLD_DAYS", "60")
    policies = _load_python_module(ARCHIVER / "config" / "policies.py", "bdx_archiver_policies")

    physical = policies.determinePolicy({"pvName": "BDX:PSU:LV1:CH1:VOLTAGE_RBV"})
    state = policies.determinePolicy({"pvName": "BDX:PSU:LV1:CH1:OUTPUT_STATE"})
    diagnostic = policies.determinePolicy({"pvName": "BDX:PSU:LV1:CH1:ERROR_MESSAGE"})

    assert physical["policyName"] == "BDX_Physical_5s"
    assert physical["samplingMethod"] == "MONITOR"
    assert physical["samplingPeriod"] == 5.0
    assert state["policyName"] == "BDX_State_Change"
    assert state["samplingMethod"] == "MONITOR"
    assert state["samplingPeriod"] == 1.0
    assert diagnostic["policyName"] == "BDX_Diagnostic_Change"
    assert diagnostic["samplingMethod"] == "MONITOR"
    assert diagnostic["samplingPeriod"] == 5.0

    data_stores = physical["dataStores"]
    medium_term = next(store for store in data_stores if "name=MTS" in store)
    long_term = next(store for store in data_stores if "name=LTS" in store)
    assert "partitionGranularity=PARTITION_DAY" in medium_term
    assert "hold=60" in medium_term
    assert "partitionGranularity=PARTITION_YEAR" in long_term
    assert "hold=" not in long_term
    assert "black" not in long_term.lower()


def test_archiver_env_documents_approved_retention_semantics():
    text = (ARCHIVER / "config" / "archappl.env.example").read_text(encoding="utf-8")

    assert "BDX_ARCHIVER_MEDIUM_TERM_HOLD_DAYS=60" in text
    assert "BDX_ARCHIVER_LONG_TERM_HOLD_YEARS" not in text
    assert "BDX_ARCHIVER_HEARTBEAT_POLICY" not in text
    assert "Long-term storage intentionally has no automatic deletion" in text
    assert "JDBM2Persistence" in text
    assert "ARCHAPPL_PERSISTENCE_LAYER_JDBM2FILENAME" in text
