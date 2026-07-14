import importlib.util
import json
import sys
import urllib.error
from pathlib import Path


SCRIPTS = Path("deploy/archiver-appliance/scripts").resolve()
PV_LISTS = Path("deploy/archiver-appliance/pv-lists")

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import archiver_common  # noqa: E402


def _load_batch_tool():
    path = SCRIPTS / "test-archive-batches.py"
    spec = importlib.util.spec_from_file_location("test_archive_batches", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


batch_tool = _load_batch_tool()


def _healthy_status() -> archiver_common.ArchiverStatus:
    return archiver_common.ArchiverStatus(
        "registered",
        "Being archived",
        connection_state=True,
        last_event="2026-07-04T10:00:00Z",
        connection_loss_regain_count=0,
    )


def _not_registered_status() -> archiver_common.ArchiverStatus:
    return archiver_common.ArchiverStatus(
        "present but not registered",
        "Not registered",
        connection_state=False,
        last_event="Never",
        connection_loss_regain_count=0,
    )


def _retrieval_ok() -> archiver_common.RetrievalResult:
    return archiver_common.RetrievalResult("successful retrieval", sample_count=3)


def test_pv_list_parser_removes_comments_blanks_and_duplicates(tmp_path: Path):
    pv_list = tmp_path / "pvs.txt"
    pv_list.write_text(
        "\n".join(
            [
                "# comment",
                "BDX:ENV:TEMP:T00:VALUE",
                "",
                "BDX:ENV:TEMP:T00:VALUE  # duplicate",
                "BDX:PSU:LV1:CH1:VOLTAGE_RBV",
            ]
        ),
        encoding="utf-8",
    )

    assert archiver_common.read_pv_list([pv_list]) == [
        "BDX:ENV:TEMP:T00:VALUE",
        "BDX:PSU:LV1:CH1:VOLTAGE_RBV",
    ]


def test_ioc_requested_intersection_preserves_missing_pvs():
    requested = [
        "BDX:ENV:TEMP:T00:VALUE",
        "BDX:ENV:TEMP:T01:VALUE",
        "BDX:ENV:TEMP:T03:VALUE",
    ]
    present, missing = batch_tool.split_present_missing(
        requested,
        ["BDX:ENV:TEMP:T01:VALUE"],
    )

    assert present == ["BDX:ENV:TEMP:T01:VALUE"]
    assert missing == ["BDX:ENV:TEMP:T00:VALUE", "BDX:ENV:TEMP:T03:VALUE"]


def test_subsystem_and_category_classification():
    assert archiver_common.subsystem_for_pv("BDX:ENV:TEMP:T01:VALUE") == "environment"
    assert archiver_common.subsystem_for_pv("BDX:PSU:LV1:CH1:OUTPUT_RBV") == "psu"
    assert archiver_common.subsystem_for_pv("BDX:CHILLER:CHILLER1:FAULT") == "chiller"

    assert archiver_common.category_for_pv("BDX:ENV:TEMP:T01:VALUE") == "physical"
    assert archiver_common.category_for_pv("BDX:PSU:LV1:CH1:OUTPUT_RBV") == "state"
    assert archiver_common.category_for_pv("BDX:CHILLER:CHILLER1:ERROR_MESSAGE") == "diagnostic"


def test_deterministic_batching_uses_expected_order():
    pvs = [
        "BDX:ENV:TEMP:T01:VALUE",
        "BDX:PSU:LV1:CH1:OUTPUT_RBV",
        "BDX:CHILLER:CHILLER1:ERROR_MESSAGE",
        "BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV",
        "BDX:PSU:LV1:CH1:VOLTAGE_RBV",
    ]
    grouped = batch_tool.group_pvs(pvs)
    batches = batch_tool.build_batches(grouped, batch_size=1)

    assert [(batch.subsystem, batch.category, batch.pvs[0]) for batch in batches] == [
        ("chiller", "physical", "BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV"),
        ("chiller", "diagnostic", "BDX:CHILLER:CHILLER1:ERROR_MESSAGE"),
        ("psu", "physical", "BDX:PSU:LV1:CH1:VOLTAGE_RBV"),
        ("psu", "state", "BDX:PSU:LV1:CH1:OUTPUT_RBV"),
        ("environment", "physical", "BDX:ENV:TEMP:T01:VALUE"),
    ]


def test_dry_run_does_not_call_archive_pv(tmp_path: Path):
    called = False

    def archive_fn(*_args):
        nonlocal called
        called = True
        raise AssertionError("archivePV must not be called in dry-run mode")

    def status_fn(_url, _pv, _timeout):
        return _not_registered_status()

    def retrieval_fn(*_args, **_kwargs):
        return archiver_common.RetrievalResult("known PV without samples", sample_count=0)

    outcome = batch_tool.run_batch(
        batch_tool.Batch(1, "chiller", "physical", ["BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV"]),
        output_dir=tmp_path,
        mgmt_url="http://127.0.0.1:17665/mgmt/bpl",
        retrieval_url="http://127.0.0.1:17668/retrieval",
        ioc_log=tmp_path / "ioc.log",
        register=False,
        wait_seconds=0,
        retrieval_minutes=20,
        timeout=0.1,
        max_new_protocol_errors=0,
        continue_on_protocol_errors=False,
        status_fn=status_fn,
        archive_fn=archive_fn,
        retrieval_fn=retrieval_fn,
    )

    assert called is False
    assert outcome.rows[0]["registration_result"] == "dry-run-not-registered"


def test_already_registered_pv_is_not_registered_again(tmp_path: Path):
    called = False

    def archive_fn(*_args):
        nonlocal called
        called = True
        return True, "submitted"

    outcome = batch_tool.run_batch(
        batch_tool.Batch(1, "psu", "physical", ["BDX:PSU:LV1:CH1:VOLTAGE_RBV"]),
        output_dir=tmp_path,
        mgmt_url="http://127.0.0.1:17665/mgmt/bpl",
        retrieval_url="http://127.0.0.1:17668/retrieval",
        ioc_log=tmp_path / "ioc.log",
        register=True,
        wait_seconds=0,
        retrieval_minutes=20,
        timeout=0.1,
        max_new_protocol_errors=0,
        continue_on_protocol_errors=False,
        status_fn=lambda *_args: _healthy_status(),
        archive_fn=archive_fn,
        retrieval_fn=lambda *_args, **_kwargs: _retrieval_ok(),
    )

    assert called is False
    assert outcome.rows[0]["registration_result"] == "already-registered"
    assert outcome.rows[0]["healthy"] is True


def test_healthy_get_pv_status_parsing():
    body = json.dumps(
        {
            "pvName": "BDX:PSU:LV1:CH1:VOLTAGE_RBV",
            "status": "Being archived",
            "connectionState": True,
            "lastEvent": "2026-07-04T10:00:00Z",
            "connectionLossRegainCount": 2,
        }
    )
    status = archiver_common.parse_archiver_status_response(
        200,
        body,
        "BDX:PSU:LV1:CH1:VOLTAGE_RBV",
    )

    assert status.status == "Being archived"
    assert status.connection_state is True
    assert status.last_event == "2026-07-04T10:00:00Z"
    assert status.connection_loss_regain_count == 2


def test_disconnected_status_parsing():
    body = json.dumps(
        {
            "status": "Being archived",
            "connectionState": False,
            "lastEvent": "2026-07-04T10:00:00Z",
        }
    )
    status = archiver_common.parse_archiver_status_response(200, body, "BDX:ENV:TEMP:T01:VALUE")

    assert status.connection_state is False
    assert batch_tool.status_failure_reason(status, _retrieval_ok()) == "disconnected"


def test_single_appliance_registration_preserves_policy_and_selects_appliance(monkeypatch):
    requested_url = ""

    def fetch(url, _timeout):
        nonlocal requested_url
        requested_url = url
        return 200, "submitted"

    monkeypatch.setattr(archiver_common, "fetch_text", fetch)

    ok, message = archiver_common.archive_pv(
        "http://127.0.0.1:17665/mgmt/bpl",
        "BDX:TEST:VALUE",
        "BDX_Physical_5s",
        1.0,
        appliance_id="bdx0",
    )

    assert ok is True
    assert message == "submitted"
    assert "pv=BDX%3ATEST%3AVALUE" in requested_url
    assert "policy=BDX_Physical_5s" in requested_url
    assert "appliance=bdx0" in requested_url


def test_never_last_event_reports_initial_sampling_incomplete():
    status = archiver_common.ArchiverStatus(
        "registered",
        "Being archived",
        connection_state=True,
        last_event="Never",
    )

    assert batch_tool.status_failure_reason(status, _retrieval_ok()) == "initial sampling incomplete"


def test_retrieval_parsing_distinguishes_samples_and_known_empty_pv():
    assert (
        archiver_common.classify_payload(
            [{"meta": {"name": "BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV"}, "data": [{"val": 20.0}]}],
            "BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV",
        ).result
        == "successful retrieval"
    )
    assert (
        archiver_common.classify_payload(
            [{"meta": {"name": "BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV"}, "data": []}],
            "BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV",
        ).result
        == "known PV without samples"
    )


def test_retrieval_endpoint_unavailable(monkeypatch):
    def fail_fetch(_url, _timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(archiver_common, "fetch_json", fail_fetch)
    result = archiver_common.verify_retrieval(
        "http://127.0.0.1:17668/retrieval",
        "BDX:ENV:TEMP:T01:VALUE",
        minutes=20,
    )

    assert result.result.startswith("endpoint failure")
    assert result.endpoint_available is False


def test_log_offset_counts_only_new_protocol_errors(tmp_path: Path):
    log = tmp_path / "ioc.log"
    log.write_text("old Unrecognized subscriptionid\n", encoding="utf-8")
    offset = batch_tool.log_offset(log)
    with log.open("a", encoding="utf-8") as stream:
        stream.write("new Unrecognized subscriptionid\n")
        stream.write("new Unknown Channel sid\n")
        stream.write("ordinary line\n")

    new_text = batch_tool.read_new_log(log, offset)

    assert "old Unrecognized subscriptionid" not in new_text
    assert batch_tool.count_protocol_errors(new_text) == 2


def test_json_and_csv_output(tmp_path: Path):
    rows = [
        {
            "batch": "batch-001-chiller-physical",
            "pv": "BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV",
            "subsystem": "chiller",
            "category": "physical",
            "policy": "BDX_Physical_5s",
            "registration_result": "already-registered",
            "archiver_status": "Being archived",
            "connectionState": True,
            "lastEvent": "2026-07-04T10:00:00Z",
            "connectionLossRegainCount": 0,
            "retrieval_result": "successful retrieval",
            "retrieved_sample_count": 1,
            "new_protocol_error_count": 0,
            "healthy": True,
            "failure_reason": "",
        }
    ]
    summary = batch_tool.summarize(
        ioc_pvs=[rows[0]["pv"]],
        requested_pvs=[rows[0]["pv"]],
        present_pvs=[rows[0]["pv"]],
        missing_pvs=[],
        rows=rows,
        total_protocol_errors=0,
    )

    batch_tool.write_summary_files(tmp_path, summary, rows)

    assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))["successful_count"] == 1
    assert "BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV" in (
        tmp_path / "summary.csv"
    ).read_text(encoding="utf-8")
    assert "Absent prototype PVs" in (tmp_path / "final-report.txt").read_text(encoding="utf-8")


def test_main_returns_nonzero_on_validation_failure(tmp_path: Path, monkeypatch):
    pv = "BDX:ENV:TEMP:T01:VALUE"
    pv_list = tmp_path / "prototype.txt"
    pv_list.write_text(pv + "\n", encoding="utf-8")

    monkeypatch.setattr(batch_tool, "generate_ioc_pvs", lambda _config_dir: [pv])

    def fail_batch(batch, **_kwargs):
        return batch_tool.BatchOutcome(
            rows=[
                {
                    "batch": batch.name,
                    "pv": pv,
                    "subsystem": "environment",
                    "category": "physical",
                    "policy": "BDX_Physical_5s",
                    "registration_result": "dry-run-not-registered",
                    "archiver_status": "Not registered",
                    "connectionState": False,
                    "lastEvent": "Never",
                    "connectionLossRegainCount": 0,
                    "retrieval_result": "known PV without samples",
                    "retrieved_sample_count": 0,
                    "new_protocol_error_count": 0,
                    "healthy": False,
                    "failure_reason": "present but not registered",
                }
            ],
            protocol_error_count=0,
            failed=True,
        )

    monkeypatch.setattr(batch_tool, "run_batch", fail_batch)

    code = batch_tool.main(
        [
            "--config-dir",
            "config/profiles/prototype",
            "--pv-list",
            str(pv_list),
            "--output-dir",
            str(tmp_path / "out"),
            "--wait-seconds",
            "0",
        ]
    )

    assert code == 1


def test_repository_pv_lists_are_not_mutated():
    prototype = PV_LISTS / "prototype.txt"
    before = prototype.read_text(encoding="utf-8")

    requested = archiver_common.read_pv_list([prototype])
    batch_tool.split_present_missing(requested, ["BDX:ENV:TEMP:T01:VALUE"])

    assert prototype.read_text(encoding="utf-8") == before
