#!/usr/bin/env python3
"""Shared helpers for BDX Archiver Appliance scripts."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_MGMT_URL = "http://127.0.0.1:17665/mgmt/bpl"
DEFAULT_RETRIEVAL_URL = "http://127.0.0.1:17668/retrieval"

PHYSICAL_POLICY = "BDX_Physical_5s"
STATE_POLICY = "BDX_State_Change"
DIAGNOSTIC_POLICY = "BDX_Diagnostic_Change"

POLICY_TO_CATEGORY = {
    PHYSICAL_POLICY: "physical",
    STATE_POLICY: "state",
    DIAGNOSTIC_POLICY: "diagnostic",
}

DISALLOWED_SUFFIXES = (
    ":APPLY_MESSAGE",
    ":APPLY_STATUS",
    ":COMM_TIMEOUT_SET",
    ":CURRENT_LIMIT_REQUEST",
    ":CURRENT_LIMIT_SET",
    ":HEARTBEAT",
    ":OCP_SET",
    ":OUTPUT_SET",
    ":OVP_SET",
    ":RUN_SET",
    ":SAFE_SETPOINT_SET",
    ":SETPOINT_REQUEST",
    ":SETPOINT_SET",
    ":VOLTAGE_REQUEST",
    ":VOLTAGE_SET",
)

STATE_SUFFIXES = (
    ":ALL_OUTPUTS_OFF",
    ":COMM_OK",
    ":COMM_STATUS",
    ":DEVIATION_ALARM",
    ":DEVIATION_STATUS",
    ":DEVIATION_WARNING",
    ":EXTERNAL_TEMPERATURE_VALID",
    ":FAULT",
    ":IOC_STATE",
    ":OUTPUT_RBV",
    ":OUTPUT_STATE",
    ":PRESSURE_VALID",
    ":RUN_RBV",
    ":RUN_STATE",
    ":STATUS",
    ":STATUS_OK",
)

DIAGNOSTIC_SUFFIXES = (
    ":COOLING_MODE",
    ":DEVICE_STATUS",
    ":ERROR_CODE",
    ":ERROR_MESSAGE",
    ":FAULT_DIAGNOSIS",
    ":LAST_TEMPERATURE_UPDATE",
    ":LAST_UPDATE",
    ":PUMP_STAGE",
)


@dataclass(frozen=True)
class ArchiverStatus:
    result: str
    status: str
    connection_state: bool | None = None
    last_event: str | None = None
    connection_loss_regain_count: int | None = None
    raw: str = ""

    @property
    def already_registered(self) -> bool:
        return self.status == "Being archived" or self.result == "registered"


@dataclass(frozen=True)
class RetrievalResult:
    result: str
    sample_count: int = 0
    endpoint_available: bool = True

    @property
    def successful(self) -> bool:
        return self.result == "successful retrieval" and self.sample_count > 0


def read_pv_list(paths: list[Path]) -> list[str]:
    """Read PV-list files, removing comments, blanks, and duplicate PVs."""
    seen: set[str] = set()
    pvs: list[str] = []
    for path in paths:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or line in seen:
                continue
            seen.add(line)
            pvs.append(line)
    return pvs


def policy_for_pv(pv: str, default_policy: str = PHYSICAL_POLICY) -> str:
    if pv.endswith(STATE_SUFFIXES):
        return STATE_POLICY
    if pv.endswith(DIAGNOSTIC_SUFFIXES):
        return DIAGNOSTIC_POLICY
    return default_policy


def category_for_pv(pv: str, default_policy: str = PHYSICAL_POLICY) -> str:
    return POLICY_TO_CATEGORY[policy_for_pv(pv, default_policy)]


def subsystem_for_pv(pv: str) -> str:
    if pv.startswith("BDX:ENV:"):
        return "environment"
    if pv.startswith("BDX:PSU:"):
        return "psu"
    if pv.startswith("BDX:CHILLER:"):
        return "chiller"
    return "unknown"


def is_archivable_pv(pv: str) -> bool:
    if pv.endswith("_CMD") or pv.endswith("_REQUEST"):
        return False
    return not pv.endswith(DISALLOWED_SUFFIXES)


def bpl_url(base_url: str, endpoint: str, params: dict[str, str]) -> str:
    return (
        base_url.rstrip("/")
        + "/"
        + endpoint.lstrip("/")
        + "?"
        + urllib.parse.urlencode(params)
    )


def fetch_text(url: str, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Accept": "application/json,text/plain,*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def fetch_json(url: str, timeout: float) -> tuple[int, Any, str]:
    status, body = fetch_text(url, timeout)
    try:
        return status, json.loads(body), body
    except json.JSONDecodeError:
        return status, None, body


def _find_status_dict(payload: Any, pv: str) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if pv in payload and isinstance(payload[pv], dict):
            return payload[pv]
        return payload
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = item.get("pvName") or item.get("pv") or item.get("name")
            if name in (None, pv):
                return item
    return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "connected", "yes", "1"}:
            return True
        if lowered in {"false", "disconnected", "no", "0"}:
            return False
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_archiver_status_response(http_status: int, body: str, pv: str) -> ArchiverStatus:
    lowered = body.lower()
    if http_status >= 500:
        return ArchiverStatus("endpoint failure", "Endpoint failure", raw=body)
    if (
        http_status == 404
        or "not being archived" in lowered
        or "not currently being archived" in lowered
        or "not found" in lowered
        or "unknown" in lowered
    ):
        return ArchiverStatus("present but not registered", "Not registered", raw=body)

    payload: Any
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        text = body.strip()
        if text:
            return ArchiverStatus("registered", text, raw=body)
        return ArchiverStatus("endpoint failure", "Empty status response", raw=body)

    item = _find_status_dict(payload, pv)
    if item is None:
        return ArchiverStatus("present but not registered", "Not registered", raw=body)

    status_text = (
        item.get("status")
        or item.get("pvStatus")
        or item.get("state")
        or item.get("statusText")
        or ""
    )
    if isinstance(status_text, dict):
        status_text = status_text.get("status") or status_text.get("state") or ""
    status = str(status_text).strip() or "Unknown"
    connection_state = _bool_or_none(
        item.get("connectionState")
        if "connectionState" in item
        else item.get("connected", item.get("isConnected"))
    )
    last_event_value = (
        item.get("lastEvent")
        or item.get("lastSampleTime")
        or item.get("lastSample")
        or item.get("lastEventTime")
    )
    last_event = None if last_event_value is None else str(last_event_value)
    loss_count = _int_or_none(
        item.get("connectionLossRegainCount")
        or item.get("connectionLosses")
        or item.get("connection_loss_regain_count")
    )
    result = "registered" if status == "Being archived" else "status returned"
    return ArchiverStatus(result, status, connection_state, last_event, loss_count, body)


def get_pv_status(mgmt_url: str, pv: str, timeout: float = 10.0) -> ArchiverStatus:
    status_url = bpl_url(mgmt_url, "getPVStatus", {"pv": pv})
    status, body = fetch_text(status_url, timeout)
    return parse_archiver_status_response(status, body, pv)


def pv_already_registered(mgmt_url: str, pv: str, timeout: float) -> bool:
    return get_pv_status(mgmt_url, pv, timeout).already_registered


def archive_pv(mgmt_url: str, pv: str, policy: str, timeout: float) -> tuple[bool, str]:
    archive_url = bpl_url(mgmt_url, "archivePV", {"pv": pv, "policy": policy})
    status, body = fetch_text(archive_url, timeout)
    if 200 <= status < 300:
        return True, body.strip() or "submitted"
    return False, f"HTTP {status}: {body.strip()}"


def retrieval_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/data/getData.json"):
        return base
    return base + "/data/getData.json"


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_fixture(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def data_blocks_for_pv(payload: Any, pv: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        if "error" in payload or "message" in payload:
            return []
        payload = [payload]
    if not isinstance(payload, list):
        return []
    blocks: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        meta = item.get("meta", {})
        block_name = item.get("name") or meta.get("name") or meta.get("pvName")
        if block_name in (None, pv):
            blocks.append(item)
    return blocks


def block_sample_count(block: dict[str, Any]) -> int:
    data = block.get("data", [])
    return len(data) if isinstance(data, list) else 0


def classify_payload(payload: Any, pv: str) -> RetrievalResult:
    text = json.dumps(payload).lower()
    if "unknown" in text or "not found" in text or "not currently being archived" in text:
        return RetrievalResult("unknown PV", 0)
    blocks = data_blocks_for_pv(payload, pv)
    if not blocks:
        return RetrievalResult("unknown PV", 0)
    sample_count = sum(block_sample_count(block) for block in blocks)
    if sample_count > 0:
        return RetrievalResult("successful retrieval", sample_count)
    return RetrievalResult("known PV without samples", 0)


def verify_retrieval(
    retrieval_url: str,
    pv: str,
    *,
    minutes: float = 10.0,
    timeout: float = 10.0,
    from_time: str | None = None,
    to_time: str | None = None,
    fixture_payload: Any | None = None,
) -> RetrievalResult:
    if fixture_payload is not None:
        return classify_payload(fixture_payload, pv)

    now = datetime.now(timezone.utc)
    start = from_time or iso_utc(now - timedelta(minutes=minutes))
    end = to_time or iso_utc(now)
    query = urllib.parse.urlencode({"pv": pv, "from": start, "to": end})
    url = retrieval_endpoint(retrieval_url) + "?" + query
    try:
        status, payload, body = fetch_json(url, timeout)
    except (OSError, urllib.error.URLError) as exc:
        return RetrievalResult(f"endpoint failure: {exc}", 0, endpoint_available=False)
    if status >= 500:
        return RetrievalResult(f"endpoint failure: HTTP {status}", 0, endpoint_available=False)
    if status == 404:
        return RetrievalResult("unknown PV", 0)
    if payload is None:
        return RetrievalResult(f"endpoint failure: invalid JSON response: {body}", 0, False)
    return classify_payload(payload, pv)
