import asyncio
import io
import json

from caproto import AlarmSeverity

from bdx_slow_control.iocs.archiver_status import (
    ArchiverStatusIOC,
    CatalogResult,
    EndpointResult,
    probe_required_pv,
    summarize_results,
)


COMPONENTS = ("mgmt", "engine", "etl", "retrieval")
ENDPOINTS = {name: f"http://127.0.0.1/{name}" for name in COMPONENTS}
REQUIRED = tuple(f"required:{index}" for index in range(18))


def results(**overrides: EndpointResult) -> dict[str, EndpointResult]:
    snapshot = {name: EndpointResult(True, True, 200) for name in COMPONENTS}
    snapshot.update(overrides)
    return snapshot


def test_status_summary_distinguishes_available_starting_degraded_and_absent():
    assert summarize_results(results())[:2] == ("AVAILABLE", True)
    assert summarize_results(
        results(mgmt=EndpointResult(False, True, 500))
    )[:2] == ("DEGRADED", False)
    assert summarize_results(
        {name: EndpointResult(False, True, 500) for name in COMPONENTS}
    )[:2] == ("STARTING", False)
    assert summarize_results(
        {name: EndpointResult(False, False) for name in COMPONENTS}
    )[:2] == ("UNAVAILABLE", False)


def test_required_catalog_probe_accepts_archiver_string_connection_state(monkeypatch):
    payload = [
        {
            "status": "Being archived",
            "connectionState": "true",
            "lastEvent": "2026-07-14T12:00:00Z",
        }
    ]
    requested = []

    def urlopen(url, timeout):
        requested.append((url, timeout))
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    assert probe_required_pv(
        "http://127.0.0.1:17665/mgmt/bpl/getVersions",
        "BDX:ENV:TEMP:T00:VALUE",
        1.0,
    )
    assert "getPVStatus?pv=BDX%3AENV%3ATEMP%3AT00%3AVALUE" in requested[0][0]


def test_component_failure_sets_dedicated_alarm_and_recovery_clears_it():
    monitor = ArchiverStatusIOC(
        prefix="BDX:ARCHIVER:", endpoints=ENDPOINTS, required_pvs=REQUIRED
    )

    asyncio.run(
        monitor.publish(
            results(engine=EndpointResult(False, False)),
            CatalogResult(0, 18, REQUIRED, "services unavailable"),
        )
    )

    assert monitor.STATUS.value == "UNAVAILABLE"
    assert monitor.OK.raw_value == 0
    assert monitor.MGMT_OK.raw_value == 1
    assert monitor.ENGINE_OK.raw_value == 0
    assert "eng=down" in monitor.ERROR_MESSAGE.value
    assert monitor.STATUS.alarm.severity == AlarmSeverity.MAJOR_ALARM

    asyncio.run(monitor.publish(results(), CatalogResult(18, 18)))

    assert monitor.STATUS.value == "AVAILABLE"
    assert monitor.OK.raw_value == 1
    assert monitor.ENGINE_OK.raw_value == 1
    assert monitor.ERROR_MESSAGE.value == ""
    assert monitor.CATALOG_STATUS.value == "18/18 required PVs healthy"
    assert monitor.STATUS.alarm.severity == AlarmSeverity.NO_ALARM


def test_complete_services_with_incomplete_required_catalog_are_degraded():
    monitor = ArchiverStatusIOC(
        prefix="BDX:ARCHIVER:", endpoints=ENDPOINTS, required_pvs=REQUIRED
    )

    asyncio.run(
        monitor.publish(
            results(),
            CatalogResult(14, 18, REQUIRED[14:]),
        )
    )

    assert monitor.STATUS.value == "DEGRADED"
    assert monitor.OK.raw_value == 0
    assert monitor.CATALOG_OK.raw_value == 0
    assert monitor.REQUIRED_TOTAL.value == 18
    assert monitor.REQUIRED_HEALTHY.value == 14
    assert monitor.CATALOG_STATUS.value == "14/18 required PVs healthy"
    assert monitor.STATUS.alarm.severity == AlarmSeverity.MINOR_ALARM


def test_catalog_recovery_clears_degraded_alarm_without_ioc_restart():
    monitor = ArchiverStatusIOC(
        prefix="BDX:ARCHIVER:", endpoints=ENDPOINTS, required_pvs=REQUIRED
    )
    asyncio.run(monitor.publish(results(), CatalogResult(17, 18, (REQUIRED[-1],))))
    asyncio.run(monitor.publish(results(), CatalogResult(18, 18)))

    assert monitor.STATUS.value == "AVAILABLE"
    assert monitor.CATALOG_OK.raw_value == 1
    assert monitor.REQUIRED_HEALTHY.value == 18


def test_polling_failure_is_reported_without_crashing_monitor():
    async def failing_checker(_endpoints, _timeout):
        raise RuntimeError("poll failed")

    monitor = ArchiverStatusIOC(
        prefix="BDX:ARCHIVER:",
        endpoints=ENDPOINTS,
        checker=failing_checker,
    )

    asyncio.run(monitor.poll_once())

    assert monitor.STATUS.value == "UNAVAILABLE"
    assert monitor.OK.raw_value == 0
    assert all(
        instance.raw_value == 0
        for instance in (
            monitor.MGMT_OK,
            monitor.ENGINE_OK,
            monitor.ETL_OK,
            monitor.RETRIEVAL_OK,
        )
    )


def test_endpoint_poll_runs_off_the_ioc_event_loop():
    async def slow_checker(_endpoints, _timeout):
        await asyncio.sleep(0.05)
        return results()

    monitor = ArchiverStatusIOC(
        prefix="BDX:ARCHIVER:",
        endpoints=ENDPOINTS,
        checker=slow_checker,
    )

    async def exercise():
        task = asyncio.create_task(monitor.poll_once())
        await asyncio.sleep(0.005)
        assert not task.done()
        await task

    asyncio.run(exercise())
    assert monitor.STATUS.value == "AVAILABLE"
