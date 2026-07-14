"""Read-only operational status for the independent Archiver Appliance."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
import json
from typing import Callable, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request

from caproto import AlarmSeverity, AlarmStatus, ChannelType
from caproto.server import PVGroup, pvproperty

from ..util import utc_timestamp


@dataclass(frozen=True)
class EndpointResult:
    """Result of one bounded Archiver readiness request."""

    ok: bool
    reachable: bool
    http_status: int | None = None


@dataclass(frozen=True)
class CatalogResult:
    """Health of the configured required PV catalog."""

    healthy: int
    total: int
    unhealthy: tuple[str, ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.healthy == self.total


def probe_endpoint(url: str, timeout: float) -> EndpointResult:
    """Query one version endpoint without raising on HTTP or connection failures."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            response.read(1)
            status = int(response.status)
            return EndpointResult(200 <= status < 300, True, status)
    except urllib.error.HTTPError as exc:
        return EndpointResult(False, True, int(exc.code))
    except (OSError, TimeoutError, urllib.error.URLError):
        return EndpointResult(False, False)


def check_archiver_endpoints(
    endpoints: Mapping[str, str],
    timeout: float,
) -> dict[str, EndpointResult]:
    """Check every configured endpoint with a timeout bounded per request."""
    return {name: probe_endpoint(url, timeout) for name, url in endpoints.items()}


async def probe_endpoint_async(url: str, timeout: float) -> EndpointResult:
    """Query one local HTTP endpoint without blocking the IOC event loop."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or not parsed.hostname:
        return EndpointResult(False, False)
    port = parsed.port or 80
    path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(parsed.hostname, port),
            timeout=timeout,
        )
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{port}\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(request.encode("ascii"))
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        status_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        fields = status_line.decode("ascii", errors="replace").split()
        status = int(fields[1]) if len(fields) >= 2 and fields[1].isdigit() else None
        return EndpointResult(
            status is not None and 200 <= status < 300,
            bool(status_line),
            status,
        )
    except (OSError, TimeoutError, asyncio.TimeoutError, ValueError):
        return EndpointResult(False, False)
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def check_archiver_endpoints_async(
    endpoints: Mapping[str, str],
    timeout: float,
) -> dict[str, EndpointResult]:
    """Check all components concurrently using bounded asynchronous sockets."""
    names = list(endpoints)
    snapshots = await asyncio.gather(
        *(probe_endpoint_async(endpoints[name], timeout) for name in names)
    )
    return dict(zip(names, snapshots, strict=True))


def probe_required_pv(mgmt_version_url: str, pv: str, timeout: float) -> bool:
    """Return whether one required PV is connected and has archived an event."""
    base = mgmt_version_url.rsplit("/", 1)[0]
    query = urllib.parse.urlencode({"pv": pv})
    url = f"{base}/getPVStatus?{query}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return False
    item = payload[0]
    last_event = str(item.get("lastEvent") or "").strip().lower()
    connection_state = item.get("connectionState")
    connected = connection_state is True or (
        isinstance(connection_state, str)
        and connection_state.strip().lower() == "true"
    )
    return (
        item.get("status") == "Being archived"
        and connected
        and last_event not in {"", "never", "none", "null"}
    )


async def check_required_catalog_async(
    mgmt_version_url: str,
    required_pvs: Sequence[str],
    timeout: float,
) -> CatalogResult:
    """Check required PVs concurrently without blocking the IOC event loop."""
    try:
        states = await asyncio.gather(
            *(
                asyncio.to_thread(probe_required_pv, mgmt_version_url, pv, timeout)
                for pv in required_pvs
            )
        )
    except Exception as exc:
        return CatalogResult(0, len(required_pvs), tuple(required_pvs), str(exc))
    unhealthy = tuple(pv for pv, healthy in zip(required_pvs, states, strict=True) if not healthy)
    return CatalogResult(len(required_pvs) - len(unhealthy), len(required_pvs), unhealthy)


def summarize_results(results: Mapping[str, EndpointResult]) -> tuple[str, bool, str]:
    """Return the public state, aggregate health, and a compact diagnostic."""
    failed = [name for name, result in results.items() if not result.ok]
    if not failed:
        return "AVAILABLE", True, ""

    ready_count = sum(result.ok for result in results.values())
    reachable_count = sum(result.reachable for result in results.values())
    if ready_count:
        state = "DEGRADED"
    elif reachable_count:
        state = "STARTING"
    else:
        state = "UNAVAILABLE"

    labels = {"mgmt": "mgmt", "engine": "eng", "etl": "etl", "retrieval": "ret"}
    details = []
    for name in failed:
        result = results[name]
        suffix = str(result.http_status) if result.http_status is not None else "down"
        details.append(f"{labels.get(name, name)}={suffix}")
    return state, False, " ".join(details)


class ArchiverStatusIOC(PVGroup):
    """Monitor Archiver endpoints; never start, stop, or repair the service."""

    STATUS = pvproperty(value="UNAVAILABLE", dtype=ChannelType.STRING, read_only=True)
    OK = pvproperty(value=False, dtype=bool, read_only=True)
    MGMT_OK = pvproperty(value=False, dtype=bool, read_only=True)
    ENGINE_OK = pvproperty(value=False, dtype=bool, read_only=True)
    ETL_OK = pvproperty(value=False, dtype=bool, read_only=True)
    RETRIEVAL_OK = pvproperty(value=False, dtype=bool, read_only=True)
    CATALOG_OK = pvproperty(value=False, dtype=bool, read_only=True)
    REQUIRED_TOTAL = pvproperty(value=0, dtype=int, read_only=True)
    REQUIRED_HEALTHY = pvproperty(value=0, dtype=int, read_only=True)
    CATALOG_STATUS = pvproperty(value="0/0 required PVs healthy", dtype=ChannelType.STRING, read_only=True)
    LAST_CHECK = pvproperty(value="", dtype=ChannelType.STRING, read_only=True)
    ERROR_MESSAGE = pvproperty(value="not checked", dtype=ChannelType.STRING, read_only=True)

    def __init__(
        self,
        *args,
        endpoints: Mapping[str, str],
        poll_interval: float = 10.0,
        request_timeout: float = 1.0,
        checker: Callable[
            [Mapping[str, str], float], Awaitable[dict[str, EndpointResult]]
        ] = check_archiver_endpoints_async,
        required_pvs: Sequence[str] = (),
        catalog_checker: Callable[
            [str, Sequence[str], float], Awaitable[CatalogResult]
        ] = check_required_catalog_async,
        **kwargs,
    ) -> None:
        expected = {"mgmt", "engine", "etl", "retrieval"}
        if set(endpoints) != expected:
            raise ValueError(
                "Archiver status endpoints must be exactly: engine, etl, mgmt, retrieval"
            )
        if poll_interval <= 0 or request_timeout <= 0:
            raise ValueError("Archiver polling period and timeout must be positive")
        self.endpoints = dict(endpoints)
        self.poll_interval = float(poll_interval)
        self.request_timeout = float(request_timeout)
        self.checker = checker
        self.required_pvs = tuple(dict.fromkeys(required_pvs))
        if len(self.required_pvs) != len(required_pvs):
            raise ValueError("Archiver required catalog contains duplicate PVs")
        self.catalog_checker = catalog_checker
        super().__init__(*args, **kwargs)

    async def publish(
        self,
        results: Mapping[str, EndpointResult],
        catalog: CatalogResult | None = None,
    ) -> None:
        """Publish one complete, internally consistent endpoint snapshot."""
        service_state, services_ok, message = summarize_results(results)
        catalog = catalog or CatalogResult(0, 0)
        if not services_ok:
            state = "UNAVAILABLE"
            ok = False
        elif not catalog.ok:
            state = "DEGRADED"
            ok = False
            catalog_message = catalog.error or (
                f"required catalog {catalog.healthy}/{catalog.total}; "
                + ", ".join(catalog.unhealthy)
            )
            message = catalog_message
        else:
            state = "AVAILABLE"
            ok = True
        if ok:
            severity = AlarmSeverity.NO_ALARM
            alarm_status = AlarmStatus.NO_ALARM
        elif services_ok and state == "DEGRADED":
            severity = AlarmSeverity.MINOR_ALARM
            alarm_status = AlarmStatus.COMM
        else:
            severity = AlarmSeverity.MAJOR_ALARM
            alarm_status = AlarmStatus.COMM

        await self.STATUS.write(value=state, severity=severity, status=alarm_status)
        await self.OK.write(value=ok, severity=severity, status=alarm_status)
        component_pvs = {
            "mgmt": self.MGMT_OK,
            "engine": self.ENGINE_OK,
            "etl": self.ETL_OK,
            "retrieval": self.RETRIEVAL_OK,
        }
        for name, instance in component_pvs.items():
            component_ok = results[name].ok
            await instance.write(
                value=component_ok,
                severity=(AlarmSeverity.NO_ALARM if component_ok else severity),
                status=(AlarmStatus.NO_ALARM if component_ok else alarm_status),
            )
        catalog_severity = AlarmSeverity.NO_ALARM if catalog.ok else severity
        catalog_alarm = AlarmStatus.NO_ALARM if catalog.ok else alarm_status
        await self.CATALOG_OK.write(
            value=catalog.ok,
            severity=catalog_severity,
            status=catalog_alarm,
        )
        await self.REQUIRED_TOTAL.write(value=catalog.total)
        await self.REQUIRED_HEALTHY.write(
            value=catalog.healthy,
            severity=catalog_severity,
            status=catalog_alarm,
        )
        await self.CATALOG_STATUS.write(
            value=f"{catalog.healthy}/{catalog.total} required PVs healthy",
            severity=catalog_severity,
            status=catalog_alarm,
        )
        await self.LAST_CHECK.write(value=utc_timestamp())
        await self.ERROR_MESSAGE.write(
            value=message,
            severity=severity,
            status=alarm_status,
        )

    async def poll_once(self) -> None:
        """Run one non-blocking poll; convert unexpected failures to alarm state."""
        try:
            results = await self.checker(self.endpoints, self.request_timeout)
        except Exception:  # The status monitor must never take down the IOC.
            results = {
                name: EndpointResult(False, False) for name in self.endpoints
            }
        if all(result.ok for result in results.values()):
            try:
                catalog = await self.catalog_checker(
                    self.endpoints["mgmt"],
                    self.required_pvs,
                    self.request_timeout,
                )
            except Exception as exc:
                catalog = CatalogResult(
                    0,
                    len(self.required_pvs),
                    self.required_pvs,
                    str(exc),
                )
        else:
            catalog = CatalogResult(
                0,
                len(self.required_pvs),
                self.required_pvs,
                "Archiver services unavailable",
            )
        await self.publish(results, catalog)

    @STATUS.startup
    async def STATUS(self, instance, async_lib):
        while True:
            await self.poll_once()
            await async_lib.library.sleep(self.poll_interval)
