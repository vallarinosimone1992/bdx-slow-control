from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import socket
import urllib.error
import urllib.parse

import pytest

from bdx_slow_control.archiver_retrieval import (
    ArchiverRetrievalError,
    build_retrieval_url,
    format_archiver_datetime,
    normalize_response,
    query_pv,
    query_pvs,
    summarize_samples,
)


PV = "BDX:ENV:TEMP:T00:VALUE"
BASE_URL = "http://127.0.0.1:17668/retrieval"
ENDPOINT = f"{BASE_URL}/data/getData.json"
START = datetime.fromtimestamp(100, tz=timezone.utc)
STOP = datetime.fromtimestamp(102.5, tz=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "archiver" / "temperature_response.json"


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status

    def getcode(self):
        return self.status

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _normalize(payload=None, start=START, stop=STOP):
    return normalize_response(
        _payload() if payload is None else payload,
        pv=PV,
        endpoint=ENDPOINT,
        start_utc=start,
        stop_utc=stop,
    )


def test_build_retrieval_url_encodes_pv_and_inclusive_bounds():
    url = build_retrieval_url(BASE_URL, PV, START, STOP)
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == ENDPOINT
    assert "pv=BDX%3AENV%3ATEMP%3AT00%3AVALUE" in url
    assert query == {
        "pv": [PV],
        "from": ["1970-01-01T00:01:40Z"],
        "to": ["1970-01-01T00:01:42.500000Z"],
    }


def test_format_archiver_datetime_converts_to_utc():
    local = datetime(2026, 7, 14, 16, 16, 4, tzinfo=timezone(timedelta(hours=2)))

    assert format_archiver_datetime(local) == "2026-07-14T14:16:04Z"


def test_valid_response_is_normalized_and_sorted():
    samples = _normalize()

    assert [sample.timestamp_ns for sample in samples] == [
        100_000_000_000,
        101_250_000_000,
        102_500_000_000,
    ]
    first = samples[0]
    assert first.pv == PV
    assert first.seconds == 100
    assert first.nanoseconds == 0
    assert first.timestamp_utc == "1970-01-01T00:01:40.000000000Z"
    assert first.value == 20.0
    assert first.status == 0
    assert first.severity == 0


def test_missing_status_and_severity_are_explicit_null():
    payload = _payload()
    payload[0]["meta"].pop("status")
    payload[0]["meta"].pop("severity")
    samples = _normalize(payload)

    assert samples[0].status is None
    assert samples[0].severity is None
    assert summarize_samples(samples)["alarm_or_unavailable_count"] == 2


def test_identical_duplicate_timestamp_is_deduplicated():
    payload = _payload()
    payload[0]["data"].append(deepcopy(payload[0]["data"][1]))

    assert len(_normalize(payload)) == 3


def test_incompatible_duplicate_timestamp_is_rejected():
    payload = _payload()
    duplicate = deepcopy(payload[0]["data"][1])
    duplicate["val"] = 99.0
    payload[0]["data"].append(duplicate)

    with pytest.raises(ArchiverRetrievalError, match="incompatible duplicate"):
        _normalize(payload)


def test_samples_outside_interval_are_filtered_but_boundaries_are_included():
    payload = _payload()
    payload[0]["data"].extend(
        [
            {"secs": 99, "nanos": 999999999, "val": 19.0},
            {"secs": 102, "nanos": 500000001, "val": 23.0},
        ]
    )

    samples = _normalize(payload)

    assert [sample.timestamp_ns for sample in samples] == [
        100_000_000_000,
        101_250_000_000,
        102_500_000_000,
    ]


def test_empty_json_list_is_rejected_with_structural_description():
    with pytest.raises(ArchiverRetrievalError, match="empty top-level list") as exc_info:
        _normalize([])

    assert PV in str(exc_info.value)
    assert ENDPOINT in str(exc_info.value)


def test_pv_without_samples_is_a_valid_empty_series():
    payload = [{"meta": {"name": PV}, "data": []}]

    assert _normalize(payload) == []
    assert summarize_samples([]) == {
        "sample_count": 0,
        "first_timestamp": None,
        "last_timestamp": None,
        "minimum": None,
        "maximum": None,
        "alarm_or_unavailable_count": 0,
        "warning": "no samples in requested interval",
    }


def test_only_samples_before_start_produce_an_empty_series():
    payload = [
        {
            "meta": {"name": PV},
            "data": [{"secs": 99, "nanos": 999999999, "val": 19.0}],
        }
    ]

    assert _normalize(payload) == []


@pytest.mark.parametrize("value", ["21.5", True, None, [21.5]])
def test_non_numeric_value_is_rejected(value):
    payload = [{"meta": {"name": PV}, "data": [{"secs": 100, "nanos": 0, "val": value}]}]

    with pytest.raises(ArchiverRetrievalError, match="val must be numeric"):
        _normalize(payload)


@pytest.mark.parametrize("nanos", [-1, 1_000_000_000])
def test_nanoseconds_outside_range_are_rejected(nanos: int):
    payload = [{"meta": {"name": PV}, "data": [{"secs": 100, "nanos": nanos, "val": 1.0}]}]

    with pytest.raises(ArchiverRetrievalError, match="nanos must be between"):
        _normalize(payload)


def test_timestamp_outside_datetime_range_is_rejected():
    payload = [
        {
            "meta": {"name": PV},
            "data": [{"secs": 10**30, "nanos": 0, "val": 1.0}],
        }
    ]

    with pytest.raises(ArchiverRetrievalError, match="invalid sample timestamp"):
        _normalize(
            payload,
            start=datetime.min.replace(tzinfo=timezone.utc),
            stop=datetime.max.replace(tzinfo=timezone.utc),
        )


def test_query_pv_rejects_invalid_json():
    def urlopen(_request, timeout):
        assert timeout == 3.0
        return FakeResponse(b"not-json")

    with pytest.raises(ArchiverRetrievalError, match="invalid JSON response"):
        query_pv(BASE_URL, PV, START, STOP, 3.0, urlopen=urlopen)


def test_query_pv_rejects_empty_response():
    with pytest.raises(ArchiverRetrievalError, match="empty response body"):
        query_pv(
            BASE_URL,
            PV,
            START,
            STOP,
            3.0,
            urlopen=lambda _request, timeout: FakeResponse(b"  \n"),
        )


def test_query_pv_reports_http_error_without_response_body():
    def urlopen(_request, timeout):
        raise urllib.error.HTTPError(ENDPOINT, 503, "Unavailable", {}, io.BytesIO(b"secret"))

    with pytest.raises(ArchiverRetrievalError, match="HTTP 503") as exc_info:
        query_pv(BASE_URL, PV, START, STOP, 3.0, urlopen=urlopen)

    assert "secret" not in str(exc_info.value)


def test_query_pv_reports_timeout():
    def urlopen(_request, timeout):
        raise TimeoutError("timed out")

    with pytest.raises(ArchiverRetrievalError, match="retrieval timed out"):
        query_pv(BASE_URL, PV, START, STOP, 3.0, urlopen=urlopen)


def test_query_pv_reports_connection_refused():
    def urlopen(_request, timeout):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    with pytest.raises(ArchiverRetrievalError, match="connection refused"):
        query_pv(BASE_URL, PV, START, STOP, 3.0, urlopen=urlopen)


def test_query_pv_reports_dns_failure():
    def urlopen(_request, timeout):
        raise urllib.error.URLError(socket.gaierror("unknown host"))

    with pytest.raises(ArchiverRetrievalError, match="DNS lookup failed"):
        query_pv(BASE_URL, PV, START, STOP, 3.0, urlopen=urlopen)


def test_query_all_pvs_preserves_configured_order():
    calls = []

    def query(retrieval_url, pv, start, stop, timeout):
        calls.append((retrieval_url, pv, start, stop, timeout))
        return _normalize()

    pvs = [PV, "BDX:ENV:TEMP:T01:VALUE"]
    result = query_pvs(BASE_URL, pvs, START, STOP, 4.0, query=query)

    assert list(result) == pvs
    assert [call[1] for call in calls] == pvs


def test_query_all_pvs_continues_after_a_valid_empty_series():
    calls = []
    empty_pv = "BDX:ENV:TEMP:T02:VALUE"

    def query(_retrieval_url, pv, _start, _stop, _timeout):
        calls.append(pv)
        return [] if pv == empty_pv else _normalize()

    pvs = [PV, empty_pv, "BDX:ENV:TEMP:T03:VALUE"]
    result = query_pvs(BASE_URL, pvs, START, STOP, 4.0, query=query)

    assert calls == pvs
    assert result[empty_pv] == []
    assert len(result[PV]) == 3
    assert len(result[pvs[-1]]) == 3


def test_response_metadata_for_a_different_pv_is_rejected():
    payload = [{"meta": {"name": "BDX:ENV:TEMP:OTHER:VALUE"}, "data": []}]

    with pytest.raises(ArchiverRetrievalError, match="different PV"):
        _normalize(payload)
