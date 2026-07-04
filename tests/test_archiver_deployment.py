import hashlib
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
        "scripts/register-pvs.py",
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
        "register-pvs.py",
        "verify-retrieval.py",
        "backup-config.sh",
        "uninstall.sh",
    ]:
        mode = (SCRIPTS / script).stat().st_mode
        assert mode & stat.S_IXUSR


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
        ":CLEAR_ERROR_CMD",
        ":APPLY_CMD",
        ":APPLY_SETPOINT_CMD",
        ":OUTPUT_SET",
        ":RUN_SET",
        ":VOLTAGE_SET",
        ":CURRENT_LIMIT_SET",
        ":OVP_SET",
        ":OCP_SET",
        ":SETPOINT_SET",
        ":VOLTAGE_REQUEST",
        ":CURRENT_LIMIT_REQUEST",
        ":SETPOINT_REQUEST",
    )
    for path in PV_LISTS.glob("*.txt"):
        for pv in _read_pvs(path):
            assert not pv.endswith(disallowed_suffixes), f"{path} archives command PV {pv}"


def test_archiver_chiller_list_excludes_disabled_optional_measurements():
    chiller_pvs = _read_pvs(PV_LISTS / "chiller.txt")

    assert "BDX:CHILLER:CHILLER1:PRESSURE_RBV" not in chiller_pvs
    assert "BDX:CHILLER:CHILLER1:EXTERNAL_TEMPERATURE_RBV" not in chiller_pvs
    assert "BDX:CHILLER:CHILLER1:PRESSURE_VALID" not in chiller_pvs
    assert "BDX:CHILLER:CHILLER1:EXTERNAL_TEMPERATURE_VALID" not in chiller_pvs


def test_archiver_register_dry_run_reports_each_pv(tmp_path: Path):
    pv_file = tmp_path / "pvs.txt"
    pv_file.write_text(
        "BDX:ENV:TEMP:T00:VALUE\nBDX:ENV:TEMP:T00:STATUS_OK\n",
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
