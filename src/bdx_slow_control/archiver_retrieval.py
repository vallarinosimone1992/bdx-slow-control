"""Standard-library client for EPICS Archiver Appliance sample retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import socket
from typing import Any, Callable, Iterable
import urllib.error
import urllib.parse
import urllib.request


NANOSECONDS_PER_SECOND = 1_000_000_000
MISSING_ALARM_FIELD = None
NO_SAMPLES_WARNING = "no samples in requested interval"


class ArchiverRetrievalError(RuntimeError):
    """Raised for an operational or structural Archiver retrieval failure."""


@dataclass(frozen=True)
class ArchivedSample:
    """One normalized scalar sample returned by the Archiver Appliance."""

    pv: str
    seconds: int
    nanoseconds: int
    timestamp_ns: int
    timestamp_utc: str
    value: float
    status: Any | None
    severity: Any | None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "pv": self.pv,
            "seconds": self.seconds,
            "nanoseconds": self.nanoseconds,
            "timestamp_ns": self.timestamp_ns,
            "timestamp_utc": self.timestamp_utc,
            "value": self.value,
            "status": self.status,
            "severity": self.severity,
        }


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def format_archiver_datetime(value: datetime) -> str:
    """Format an aware timestamp as an Archiver-compatible UTC instant."""
    utc_value = _require_aware(value, "timestamp")
    timespec = "microseconds" if utc_value.microsecond else "seconds"
    return utc_value.isoformat(timespec=timespec).replace("+00:00", "Z")


def retrieval_endpoint(retrieval_url: str) -> str:
    """Return the JSON sample endpoint below a retrieval base URL."""
    base = retrieval_url.strip().rstrip("/")
    if not base:
        raise ValueError("retrieval_url must not be empty")
    if base.endswith("/data/getData.json"):
        return base
    return f"{base}/data/getData.json"


def build_retrieval_url(
    retrieval_url: str,
    pv: str,
    start_utc: datetime,
    stop_utc: datetime,
) -> str:
    """Build one encoded inclusive retrieval URL."""
    start = _require_aware(start_utc, "start_utc")
    stop = _require_aware(stop_utc, "stop_utc")
    if stop < start:
        raise ValueError("stop_utc must not precede start_utc")
    query = urllib.parse.urlencode(
        {
            "pv": pv,
            "from": format_archiver_datetime(start),
            "to": format_archiver_datetime(stop),
        }
    )
    return f"{retrieval_endpoint(retrieval_url)}?{query}"


def datetime_to_timestamp_ns(value: datetime) -> int:
    """Convert an aware datetime to integer nanoseconds since the Unix epoch."""
    utc_value = _require_aware(value, "timestamp")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc_value - epoch
    return (
        (delta.days * 86400 + delta.seconds) * NANOSECONDS_PER_SECOND
        + delta.microseconds * 1000
    )


def _timestamp_utc(seconds: int, nanoseconds: int) -> str:
    try:
        base = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError) as exc:
        raise ValueError(f"seconds is outside the supported datetime range: {seconds}") from exc
    return f"{base:%Y-%m-%dT%H:%M:%S}.{nanoseconds:09d}Z"


def _structure_description(payload: Any) -> str:
    if isinstance(payload, list):
        if not payload:
            return "an empty top-level list"
        first = payload[0]
        if isinstance(first, dict):
            keys = ", ".join(sorted(str(key) for key in first)[:8])
            return f"a top-level list whose first item has keys [{keys}]"
        return f"a top-level list whose first item is {type(first).__name__}"
    if isinstance(payload, dict):
        keys = ", ".join(sorted(str(key) for key in payload)[:8])
        return f"a top-level object with keys [{keys}]"
    return f"a top-level {type(payload).__name__}"


def _error(pv: str, endpoint: str, message: str) -> ArchiverRetrievalError:
    return ArchiverRetrievalError(f"PV {pv} at {endpoint}: {message}")


def _strict_integer(value: Any, field_name: str, pv: str, endpoint: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(pv, endpoint, f"sample {field_name} must be an integer")
    return value


def _numeric_value(value: Any, pv: str, endpoint: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(pv, endpoint, "sample val must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise _error(pv, endpoint, "sample val must be finite")
    return normalized


def _normalize_record(
    record: Any,
    *,
    pv: str,
    endpoint: str,
    metadata: dict[str, Any],
) -> ArchivedSample:
    if not isinstance(record, dict):
        raise _error(pv, endpoint, f"sample record must be an object, got {type(record).__name__}")
    missing = [field for field in ("secs", "nanos", "val") if field not in record]
    if missing:
        raise _error(pv, endpoint, f"sample record is missing fields: {', '.join(missing)}")

    seconds = _strict_integer(record["secs"], "secs", pv, endpoint)
    nanoseconds = _strict_integer(record["nanos"], "nanos", pv, endpoint)
    if not 0 <= nanoseconds < NANOSECONDS_PER_SECOND:
        raise _error(pv, endpoint, "sample nanos must be between 0 and 999999999")
    value = _numeric_value(record["val"], pv, endpoint)
    try:
        timestamp_utc = _timestamp_utc(seconds, nanoseconds)
    except ValueError as exc:
        raise _error(pv, endpoint, f"invalid sample timestamp: {exc}") from exc

    return ArchivedSample(
        pv=pv,
        seconds=seconds,
        nanoseconds=nanoseconds,
        timestamp_ns=seconds * NANOSECONDS_PER_SECOND + nanoseconds,
        timestamp_utc=timestamp_utc,
        value=value,
        status=record.get("status", metadata.get("status", MISSING_ALARM_FIELD)),
        severity=record.get("severity", metadata.get("severity", MISSING_ALARM_FIELD)),
    )


def normalize_response(
    payload: Any,
    *,
    pv: str,
    endpoint: str,
    start_utc: datetime,
    stop_utc: datetime,
) -> list[ArchivedSample]:
    """Validate, filter, sort, and deduplicate one Archiver JSON response."""
    start_ns = datetime_to_timestamp_ns(start_utc)
    stop_ns = datetime_to_timestamp_ns(stop_utc)
    if stop_ns < start_ns:
        raise ValueError("stop_utc must not precede start_utc")
    if not isinstance(payload, list) or not payload:
        raise _error(
            pv,
            endpoint,
            f"unsupported response structure: {_structure_description(payload)}; expected a non-empty list",
        )

    samples: list[ArchivedSample] = []
    for block in payload:
        if not isinstance(block, dict):
            raise _error(
                pv,
                endpoint,
                f"unsupported response structure: {_structure_description(payload)}",
            )
        metadata = block.get("meta", {})
        if not isinstance(metadata, dict):
            raise _error(pv, endpoint, "response meta must be an object when present")
        block_pv = block.get("name", metadata.get("name", metadata.get("pvName")))
        if block_pv not in (None, pv):
            raise _error(pv, endpoint, f"response metadata names a different PV: {block_pv}")
        data = block.get("data")
        if not isinstance(data, list):
            raise _error(pv, endpoint, "response block must contain a data list")
        for record in data:
            sample = _normalize_record(record, pv=pv, endpoint=endpoint, metadata=metadata)
            if start_ns <= sample.timestamp_ns <= stop_ns:
                samples.append(sample)

    samples.sort(key=lambda sample: sample.timestamp_ns)
    deduplicated: list[ArchivedSample] = []
    for sample in samples:
        if deduplicated and sample.timestamp_ns == deduplicated[-1].timestamp_ns:
            if sample != deduplicated[-1]:
                raise _error(
                    pv,
                    endpoint,
                    f"incompatible duplicate samples at {sample.timestamp_utc}",
                )
            continue
        deduplicated.append(sample)
    return deduplicated


UrlOpen = Callable[..., Any]


def _network_error(pv: str, endpoint: str, exc: BaseException) -> ArchiverRetrievalError:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, (TimeoutError, socket.timeout)):
        message = "retrieval timed out"
    elif isinstance(reason, ConnectionRefusedError):
        message = "connection refused"
    elif isinstance(reason, socket.gaierror):
        message = "DNS lookup failed"
    else:
        message = f"network error: {reason}"
    return _error(pv, endpoint, message)


def query_pv(
    retrieval_url: str,
    pv: str,
    start_utc: datetime,
    stop_utc: datetime,
    timeout: float,
    *,
    urlopen: UrlOpen = urllib.request.urlopen,
) -> list[ArchivedSample]:
    """Retrieve and normalize one PV over an inclusive UTC interval."""
    endpoint = build_retrieval_url(retrieval_url, pv, start_utc, stop_utc)
    request = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise _error(pv, endpoint, f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise _network_error(pv, endpoint, exc) from exc

    if not 200 <= int(status) < 300:
        raise _error(pv, endpoint, f"HTTP {status}")
    if not body or not body.strip():
        raise _error(pv, endpoint, "empty response body")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(pv, endpoint, "invalid JSON response") from exc
    return normalize_response(
        payload,
        pv=pv,
        endpoint=endpoint,
        start_utc=start_utc,
        stop_utc=stop_utc,
    )


QueryPv = Callable[[str, str, datetime, datetime, float], list[ArchivedSample]]


def query_pvs(
    retrieval_url: str,
    pvs: Iterable[str],
    start_utc: datetime,
    stop_utc: datetime,
    timeout: float,
    *,
    query: QueryPv = query_pv,
) -> dict[str, list[ArchivedSample]]:
    """Retrieve every configured PV without interpolation or synchronization."""
    return {
        pv: query(retrieval_url, pv, start_utc, stop_utc, timeout)
        for pv in pvs
    }


def _alarm_field_is_nominal(value: Any | None) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        return value.strip().upper().replace(" ", "_") == "NO_ALARM"
    return False


def summarize_samples(samples: list[ArchivedSample]) -> dict[str, Any]:
    """Return a uniform summary for one normalized sample list."""
    if not samples:
        return {
            "sample_count": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "minimum": None,
            "maximum": None,
            "alarm_or_unavailable_count": 0,
            "warning": NO_SAMPLES_WARNING,
        }
    values = [sample.value for sample in samples]
    alarm_or_unavailable = sum(
        not _alarm_field_is_nominal(sample.status)
        or not _alarm_field_is_nominal(sample.severity)
        for sample in samples
    )
    return {
        "sample_count": len(samples),
        "first_timestamp": samples[0].timestamp_utc,
        "last_timestamp": samples[-1].timestamp_utc,
        "minimum": min(values),
        "maximum": max(values),
        "alarm_or_unavailable_count": alarm_or_unavailable,
        "warning": None,
    }
