import importlib.util
import json
from pathlib import Path
import sys


SCRIPTS = Path("deploy/archiver-appliance/scripts").resolve()
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("repair_archiver", SCRIPTS / "repair_archiver.py")
assert spec is not None and spec.loader is not None
repair_archiver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = repair_archiver
spec.loader.exec_module(repair_archiver)

from archiver_common import ArchiverStatus  # noqa: E402


def healthy_status() -> ArchiverStatus:
    return ArchiverStatus(
        "registered",
        "Being archived",
        connection_state=True,
        last_event="2026-07-13T16:00:00Z",
    )


def missing_status() -> ArchiverStatus:
    return ArchiverStatus("present but not registered", "Not registered")


class BatchClient:
    def __init__(
        self,
        initial: dict[str, ArchiverStatus],
        *,
        drain_after: int = 1,
        fail_health_after: int | None = None,
        extra_registered: tuple[str, ...] = (),
    ):
        self.statuses = dict(initial)
        self.drain_after = drain_after
        self.active: list[str] = []
        self.queue_reads = 0
        self.total_queue_reads = 0
        self.submitted: list[tuple[str, str]] = []
        self.aborted: list[str] = []
        self.paused: list[str] = []
        self.resumed: list[str] = []
        self.retrieved: list[str] = []
        self.overlap = False
        self.health_checks = 0
        self.fail_health_after = fail_health_after
        self.status_reads: list[str] = []
        self.type_reads: list[str] = []
        self.restart_calls = 0
        self.deleted: list[str] = []
        self.policy_changes: list[tuple[str, str]] = []
        self.type_infos = {
            pv: repair_archiver.PolicyInfo(
                repair_archiver.policy_for_pv(pv), "MONITOR",
                repair_archiver.POLICY_SETTINGS[repair_archiver.policy_for_pv(pv)][1],
            )
            for pv in initial
        }
        self.extra_registered = list(extra_registered)

    def require_healthy(self):
        self.health_checks += 1
        if self.fail_health_after is not None and self.health_checks > self.fail_health_after:
            raise repair_archiver.InfrastructureFailure("engine unavailable")

    def restart_engine(self):
        self.restart_calls += 1

    def registered_pvs(self):
        registered = {
            pv
            for pv, status in self.statuses.items()
            if repair_archiver.classify_status(status) != repair_archiver.NOT_ARCHIVED
        }
        return sorted(registered | set(self.extra_registered))

    def delete(self, pv):
        self.deleted.append(pv)
        raise AssertionError("repair must never delete an archived PV")

    def workflows(self):
        if not self.active:
            return []
        self.queue_reads += 1
        self.total_queue_reads += 1
        if self.queue_reads > self.drain_after:
            for pv in self.active:
                self.statuses[pv] = healthy_status()
            self.active = []
            return []
        return [repair_archiver.Workflow(pv, "METAINFO_GATHERING") for pv in self.active]

    def status(self, pv):
        self.status_reads.append(pv)
        return self.statuses[pv]

    def type_info(self, pv):
        self.type_reads.append(pv)
        return self.type_infos.get(pv)

    def change_archival_parameters(self, pv, policy):
        self.policy_changes.append((pv, policy))
        method, period = repair_archiver.POLICY_SETTINGS[policy]
        self.type_infos[pv] = repair_archiver.PolicyInfo(policy, method, period)
        return True, "changed"

    def submit(self, pv, policy):
        if len(self.active) >= 2:
            self.overlap = True
        self.submitted.append((pv, policy))
        self.active.append(pv)
        method, period = repair_archiver.POLICY_SETTINGS[policy]
        self.type_infos[pv] = repair_archiver.PolicyInfo(policy, method, period)
        self.queue_reads = 0
        return True, "submitted"

    def abort(self, pv):
        self.aborted.append(pv)
        self.active = [item for item in self.active if item != pv]
        return True, "aborted"

    def pause(self, pv):
        self.paused.append(pv)
        self.statuses[pv] = ArchiverStatus("status returned", "Paused")
        return True, "paused"

    def resume(self, pv):
        self.resumed.append(pv)
        self.statuses[pv] = ArchiverStatus(
            "registered", "Being archived", connection_state=False, last_event="Never"
        )
        self.active.append(pv)
        self.queue_reads = 0
        return True, "resumed"

    def retrieve(self, pv, minutes, *, from_time=None):
        self.retrieved.append(pv)
        return True, "retrieval ok"


def coordinator(client, pvs, **kwargs):
    options = {
        "batch_size": 1,
        "queue_timeout": 2,
        "validation_timeout": 2,
        "poll_interval": 0,
        "sleep": lambda _seconds: None,
        "output": lambda _message: None,
    }
    options.update(kwargs)
    return repair_archiver.CatalogRepair(client, pvs, **options)


def test_healthy_pvs_are_not_reregistered_and_only_missing_pvs_are_submitted():
    client = BatchClient({"healthy": healthy_status(), "missing": missing_status()})

    assert coordinator(client, ["healthy", "missing"]).repair()

    assert [pv for pv, _policy in client.submitted] == ["missing"]
    assert client.retrieved == ["missing"]
    assert client.paused == []
    assert client.resumed == []


def test_all_problematic_pvs_start_before_global_polling():
    pvs = ["pv1", "pv2", "pv3", "pv4", "pv5"]
    client = BatchClient({pv: missing_status() for pv in pvs}, drain_after=2)

    assert coordinator(client, pvs).repair()

    assert client.overlap
    assert [pv for pv, _policy in client.submitted] == pvs
    assert client.total_queue_reads >= 3
    assert client.retrieved == pvs


class AdvancingClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        self.value += 1.0
        return self.value


class SleepClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = 0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps += 1
        self.value += seconds


def test_verification_timeout_is_global_not_multiplied_by_pv_count():
    pvs = [f"pv{index}" for index in range(10)]
    client = NeverHealthyClient({pv: missing_status() for pv in pvs})
    clock = SleepClock()

    result = coordinator(
        client,
        pvs,
        validation_timeout=3,
        poll_interval=1,
        clock=clock,
        sleep=clock.sleep,
    ).repair()

    assert not result
    assert clock.sleeps <= 6  # two shared three-second waves, not 10 PV timeouts
    assert client.health_checks < 20


def test_unexpected_overlapping_workflow_is_a_global_failure():
    client = BatchClient({"batch": missing_status(), "other": missing_status()}, drain_after=999)
    client.active = ["batch", "other"]
    repair = coordinator(client, ["batch", "other"], clock=AdvancingClock())

    try:
        repair.wait_for_idle("batch", abort_on_timeout=True)
    except repair_archiver.InfrastructureFailure as exc:
        assert "unexpected overlapping" in str(exc)
    else:
        raise AssertionError("overlapping workflow was not rejected")


def test_registration_uses_existing_policy():
    client = BatchClient({"BDX:TEST:RUN_STATE": missing_status()})
    repair = coordinator(client, ["BDX:TEST:RUN_STATE"])

    assert repair.repair()

    assert client.submitted == [("BDX:TEST:RUN_STATE", "BDX_State_Change")]


def test_pending_workflow_is_not_submitted_again():
    client = BatchClient({"pending": missing_status()}, drain_after=999)
    client.active = ["pending"]

    result = coordinator(
        client, ["pending"], clock=AdvancingClock()
    ).repair()

    assert not result
    assert [pv for pv, _policy in client.submitted].count("pending") <= 1
    assert result.outcomes[0].attempts <= 1


def test_wrong_effective_policy_is_corrected_without_pause_resume():
    client = BatchClient({"pv": healthy_status()})
    client.type_infos["pv"] = repair_archiver.PolicyInfo(
        "wrong-policy", "SCAN", 1.0
    )

    assert coordinator(client, ["pv"]).repair()

    assert client.policy_changes == [("pv", repair_archiver.PHYSICAL_POLICY)]
    assert client.paused == []
    assert client.resumed == []


def test_paused_pv_is_resumed_only_when_needed():
    client = BatchClient({"pv": ArchiverStatus("status returned", "Paused")})

    assert coordinator(client, ["pv"]).repair()

    assert client.paused == []
    assert client.resumed == ["pv"]


class NeverHealthyClient(BatchClient):
    def workflows(self):
        for pv in self.active:
            self.statuses[pv] = ArchiverStatus(
                "registered",
                "Being archived",
                connection_state=False,
                last_event="Never",
            )
        self.active = []
        return []


def test_persistent_failure_is_recorded_and_next_pv_is_attempted():
    client = NeverHealthyClient(
        {"first": missing_status(), "second": missing_status()}
    )

    result = coordinator(
        client, ["first", "second"], clock=AdvancingClock()
    ).repair()

    assert not result
    assert [pv for pv, _policy in client.submitted] == ["first", "second"]
    assert client.paused == ["first", "second"]
    assert client.resumed == ["first", "second"]
    assert [item.pv for item in result.outcomes if item.outcome == "failed"] == [
        "first",
        "second",
    ]
    assert all(item.attempts == 2 for item in result.outcomes)
    assert client.restart_calls == 0
    assert len(result.final_entries) == 2


def test_explicit_registered_unhealthy_pv_is_reactivated_not_reregistered():
    unhealthy = ArchiverStatus(
        "registered", "Being archived", connection_state=False, last_event="Never"
    )
    client = BatchClient({"unhealthy": unhealthy})

    assert coordinator(client, ["unhealthy"]).repair(["unhealthy"])

    assert client.submitted == []
    assert client.paused == ["unhealthy"]
    assert client.resumed == ["unhealthy"]


class FirstRetrievalFailsClient(BatchClient):
    def retrieve(self, pv, minutes, *, from_time=None):
        self.retrieved.append(pv)
        return len(self.retrieved) > 1, "transient retrieval failure"


def test_failed_retrieval_is_retried_individually_once():
    client = FirstRetrievalFailsClient({"missing": missing_status()})

    assert coordinator(client, ["missing"]).repair()

    assert [pv for pv, _policy in client.submitted] == ["missing"]
    assert client.retrieved == ["missing", "missing"]


class RetrievalAlwaysFailsClient(BatchClient):
    def retrieve(self, pv, minutes, *, from_time=None):
        self.retrieved.append(pv)
        return False, "persistent retrieval failure"


def test_multiple_persistent_retrieval_failures_are_accumulated():
    client = RetrievalAlwaysFailsClient(
        {"first": missing_status(), "second": missing_status()}
    )

    result = coordinator(
        client, ["first", "second"], clock=AdvancingClock()
    ).repair()

    assert not result
    assert [pv for pv, _policy in client.submitted] == ["first", "second"]
    assert set(client.retrieved) == {"first", "second"}
    assert client.retrieved.count("first") == client.retrieved.count("second")
    assert client.retrieved.count("first") >= 2
    failures = [item for item in result.outcomes if item.outcome == "failed"]
    assert [item.pv for item in failures] == ["first", "second"]
    assert {item.failure_stage for item in failures} == {"retrieval verification"}


def test_fail_fast_compatibility_stops_before_next_pv_and_still_audits():
    client = RetrievalAlwaysFailsClient(
        {"first": missing_status(), "second": missing_status()}
    )

    result = coordinator(client, ["first", "second"]).repair(
        stop_on_first_failure=True
    )

    assert not result
    assert [pv for pv, _policy in client.submitted] == ["first"]
    assert len(result.final_entries) == 2
    assert client.status_reads.count("second") >= 2


def test_global_endpoint_failure_stops_immediately_without_final_audit():
    client = BatchClient(
        {"first": missing_status(), "second": missing_status()}, fail_health_after=2
    )

    result = coordinator(client, ["first", "second"]).repair()

    assert not result.completed
    assert result.global_error == "engine unavailable"
    assert [pv for pv, _policy in client.submitted] == ["first", "second"]
    assert result.final_entries == []


def test_catalog_classification_covers_required_categories():
    assert repair_archiver.classify_status(healthy_status()) == repair_archiver.HEALTHY
    assert (
        repair_archiver.classify_status(
            ArchiverStatus("registered", "Being archived", False, "Never")
        )
        == repair_archiver.REGISTERED_UNHEALTHY
    )
    assert repair_archiver.classify_status(healthy_status(), pending=True) == repair_archiver.PENDING
    assert repair_archiver.classify_status(missing_status()) == repair_archiver.NOT_ARCHIVED
    assert (
        repair_archiver.classify_status(ArchiverStatus("status returned", "Paused"))
        == repair_archiver.PAUSED
    )
    assert (
        repair_archiver.classify_status(ArchiverStatus("endpoint failure", "Endpoint failure"))
        == repair_archiver.UNKNOWN
    )


def test_audit_only_cli_does_not_submit_or_abort(monkeypatch, tmp_path):
    pv_list = tmp_path / "pvs.txt"
    pv_list.write_text("healthy\nmissing\n", encoding="utf-8")
    client = BatchClient({"healthy": healthy_status(), "missing": missing_status()})
    monkeypatch.setattr(repair_archiver, "ArchiverClient", lambda *_args: client)

    result = repair_archiver.main(["--audit-only", str(pv_list)])

    assert result == 1
    assert client.submitted == []
    assert client.aborted == []


def test_out_of_scope_registrations_are_reported_but_do_not_fail_required_audit():
    client = BatchClient(
        {
            "required": healthy_status(),
            "legacy": healthy_status(),
        },
        extra_registered=("legacy",),
    )
    repair = coordinator(client, ["required"])

    result = repair.repair()

    assert result
    assert result.initial_out_of_scope == ["legacy"]
    assert result.final_out_of_scope == ["legacy"]
    assert client.submitted == []


def test_safe_cleanup_pauses_extras_without_deleting_or_purging_history():
    client = BatchClient(
        {
            "required": healthy_status(),
            "legacy": healthy_status(),
        },
        extra_registered=("legacy",),
    )

    result = coordinator(client, ["required"]).repair(pause_out_of_scope=True)

    assert result
    assert result.paused_out_of_scope == ["legacy"]
    assert result.final_out_of_scope == ["legacy"]
    assert client.paused == ["legacy"]
    assert client.deleted == []
    assert client.statuses["legacy"].status == "Paused"

    payload = repair_archiver.report_payload(result, ["required"])
    assert payload["summary"]["out_of_scope_registered"] == 1
    assert payload["final_out_of_scope_registered"] == ["legacy"]
    assert payload["paused_out_of_scope_during_run"] == ["legacy"]


def test_repair_source_contains_no_delete_or_storage_removal_operation():
    source = (SCRIPTS / "repair_archiver.py").read_text(encoding="utf-8")

    assert "deletePV" not in source
    assert "purge" not in source.lower()
    assert "rmtree" not in source
    assert ".unlink(" not in source


def test_repair_attempts_only_required_catalog_and_excludes_diagnostics():
    required = ["BDX:PSU:LV1:CH1:VOLTAGE_RBV"]
    excluded = "BDX:PSU:LV1:CH1:LAST_UPDATE"
    client = BatchClient(
        {
            required[0]: missing_status(),
            excluded: healthy_status(),
        },
        extra_registered=(excluded,),
    )

    result = coordinator(client, required).repair()

    assert result
    assert [pv for pv, _policy in client.submitted] == required
    assert excluded not in client.status_reads
    assert result.final_out_of_scope == [excluded]


def test_cli_exit_zero_only_for_fully_healthy_catalog_and_writes_json(
    monkeypatch, tmp_path
):
    pv_list = tmp_path / "pvs.txt"
    pv_list.write_text(
        "\n".join(repair_archiver.DEFAULT_REPRESENTATIVES) + "\n",
        encoding="utf-8",
    )
    client = BatchClient(
        {pv: healthy_status() for pv in repair_archiver.DEFAULT_REPRESENTATIVES}
    )
    monkeypatch.setattr(repair_archiver, "ArchiverClient", lambda *_args: client)
    report = tmp_path / "report.json"

    result = repair_archiver.main(["--report-path", str(report), str(pv_list)])

    assert result == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["fully_healthy"] is True
    assert len(payload["pv_outcomes"]) == len(repair_archiver.DEFAULT_REPRESENTATIVES)
    assert {item["outcome"] for item in payload["pv_outcomes"]} == {
        "already healthy"
    }


def test_cli_exit_one_for_completed_partial_success_and_json_has_every_pv(
    monkeypatch, tmp_path
):
    pv_list = tmp_path / "pvs.txt"
    pv_list.write_text("first\nsecond\n", encoding="utf-8")
    client = NeverHealthyClient({"first": missing_status(), "second": missing_status()})
    monkeypatch.setattr(repair_archiver, "ArchiverClient", lambda *_args: client)
    report = tmp_path / "report.json"

    result = repair_archiver.main(
        [
            "--queue-timeout",
            "0",
            "--validation-timeout",
            "0",
            "--poll-interval",
            "0",
            "--report-path",
            str(report),
            str(pv_list),
        ]
    )

    assert result == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["completed"] is True
    assert payload["summary"]["failed_during_run"] == 2
    assert [item["pv"] for item in payload["pv_outcomes"]] == ["first", "second"]


def test_cli_exit_two_for_global_infrastructure_failure(monkeypatch, tmp_path):
    pv_list = tmp_path / "pvs.txt"
    pv_list.write_text("first\nsecond\n", encoding="utf-8")
    client = BatchClient(
        {"first": missing_status(), "second": missing_status()}, fail_health_after=0
    )
    monkeypatch.setattr(repair_archiver, "ArchiverClient", lambda *_args: client)
    report = tmp_path / "report.json"

    result = repair_archiver.main(["--report-path", str(report), str(pv_list)])

    assert result == 2
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["completed"] is False
    assert payload["global_error"] == "engine unavailable"
