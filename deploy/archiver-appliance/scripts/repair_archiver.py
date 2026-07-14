#!/usr/bin/env python3
"""Selective, staged Archiver Appliance catalog audit and repair."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
import urllib.error

from archiver_common import (
    DEFAULT_MGMT_URL,
    DEFAULT_RETRIEVAL_URL,
    PHYSICAL_POLICY,
    ArchiverStatus,
    archive_pv,
    bpl_url,
    fetch_json,
    fetch_text,
    get_pv_status,
    policy_for_pv,
    read_pv_list,
    verify_retrieval,
)


HEALTHY = "healthy"
REGISTERED_UNHEALTHY = "registered but unhealthy"
PENDING = "initial sampling or pending workflow"
NOT_ARCHIVED = "not being archived"
PAUSED = "paused"
UNVERIFIABLE = "unverifiable"
UNKNOWN = "unknown/error"
CATEGORIES = (
    HEALTHY,
    REGISTERED_UNHEALTHY,
    PENDING,
    NOT_ARCHIVED,
    PAUSED,
    UNVERIFIABLE,
    UNKNOWN,
)
NEVER_VALUES = {"", "never", "none", "null"}
DEFAULT_REPRESENTATIVES = (
    "BDX:PSU:LV1:CH1:VOLTAGE_RBV",
    "BDX:PSU:LV2:CH2:CURRENT_RBV",
    "BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV",
    "BDX:ENV:TEMP:T00:VALUE",
)
DEFAULT_READY_URLS = {
    "management": "http://127.0.0.1:17665/mgmt/bpl/getVersions",
    "engine": "http://127.0.0.1:17666/engine/bpl/getVersion",
    "etl": "http://127.0.0.1:17667/etl/bpl/getVersion",
    "retrieval": "http://127.0.0.1:17668/retrieval/bpl/getVersion",
}


class InfrastructureFailure(RuntimeError):
    """A global failure that makes continued repair unsafe."""


@dataclass(frozen=True)
class AttemptFailure:
    stage: str
    reason: str


@dataclass(frozen=True)
class PolicyInfo:
    name: str
    sampling_method: str
    sampling_period: float


POLICY_SETTINGS = {
    PHYSICAL_POLICY: ("MONITOR", 5.0),
    "BDX_State_Change": ("MONITOR", 1.0),
    "BDX_Diagnostic_Change": ("MONITOR", 5.0),
}


@dataclass
class PVOutcome:
    pv: str
    initial_category: str
    outcome: str
    attempts: int
    action: str
    started_at: str
    completed_at: str
    final_category: str | None = None
    failure_stage: str | None = None
    diagnostic: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pv": self.pv,
            "initial_category": self.initial_category,
            "final_category": self.final_category,
            "outcome": self.outcome,
            "attempts": self.attempts,
            "action": self.action,
            "failure_stage": self.failure_stage,
            "diagnostic": self.diagnostic,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class RepairResult:
    started_at: str
    completed_at: str
    outcomes: list[PVOutcome]
    initial_entries: list["CatalogEntry"]
    final_entries: list["CatalogEntry"]
    initial_out_of_scope: list[str]
    final_out_of_scope: list[str]
    paused_out_of_scope: list[str]
    completed: bool
    global_error: str | None = None

    @property
    def fully_healthy(self) -> bool:
        return (
            self.completed
            and not self.global_error
            and len(self.final_entries) == len(self.initial_entries)
            and all(entry.category == HEALTHY for entry in self.final_entries)
            and all(item.outcome != "failed" for item in self.outcomes)
        )

    def __bool__(self) -> bool:
        return self.fully_healthy


@dataclass(frozen=True)
class Workflow:
    pv: str
    state: str


@dataclass(frozen=True)
class CatalogEntry:
    pv: str
    category: str
    status: ArchiverStatus
    policy: PolicyInfo | None = None
    diagnostic: str | None = None


def policy_is_correct(info: PolicyInfo | None, expected_policy: str) -> bool:
    """Compare effective policy parameters; the AA label can remain stale after a change."""
    if info is None:
        return False
    method, period = POLICY_SETTINGS[expected_policy]
    return info.sampling_method == method and abs(info.sampling_period - period) < 1e-6


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def has_real_last_event(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in NEVER_VALUES


def classify_status(status: ArchiverStatus, *, pending: bool = False) -> str:
    text = status.status.strip().lower()
    if pending or "initial sampling" in text or "sampling" in text and "initial" in text:
        return PENDING
    if "pause" in text:
        return PAUSED
    if text == "being archived":
        if status.connection_state is True and has_real_last_event(status.last_event):
            return HEALTHY
        return REGISTERED_UNHEALTHY
    if (
        status.result == "present but not registered"
        or "not registered" in text
        or "not being archived" in text
        or "not currently being archived" in text
    ):
        return NOT_ARCHIVED
    return UNKNOWN


def _workflow_items(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("workflows", "requests", "pvs", "value"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


class ArchiverClient:
    def __init__(
        self,
        mgmt_url: str,
        retrieval_url: str,
        timeout: float,
        appliance_id: str | None = None,
        ready_urls: Mapping[str, str] | None = None,
    ) -> None:
        self.mgmt_url = mgmt_url
        self.retrieval_url = retrieval_url
        self.timeout = timeout
        self.appliance_id = appliance_id
        self.ready_urls = dict(ready_urls or DEFAULT_READY_URLS)

    def require_healthy(self) -> None:
        failures = []
        for component, url in self.ready_urls.items():
            try:
                status, body = fetch_text(url, self.timeout)
            except (OSError, urllib.error.URLError) as exc:
                failures.append(f"{component}: {exc}")
                continue
            if status < 200 or status >= 300 or not body.strip():
                failures.append(f"{component}: HTTP {status}")
        if failures:
            raise InfrastructureFailure(
                "Archiver component health check failed: " + "; ".join(failures)
            )

    def status(self, pv: str) -> ArchiverStatus:
        return get_pv_status(self.mgmt_url, pv, self.timeout)

    def workflows(self) -> list[Workflow]:
        url = self.mgmt_url.rstrip("/") + "/getNeverConnectedPVsForThisAppliance"
        status, payload, body = fetch_json(url, self.timeout)
        if status >= 400 or payload is None:
            raise RuntimeError(f"workflow query failed: HTTP {status}: {body.strip()}")
        result: list[Workflow] = []
        for item in _workflow_items(payload):
            pv = item.get("pvName") or item.get("pv") or item.get("name")
            state = item.get("currentState") or item.get("state") or item.get("status")
            if pv:
                result.append(Workflow(str(pv), str(state or "UNKNOWN")))
        return result

    def registered_pvs(self) -> list[str]:
        url = self.mgmt_url.rstrip("/") + "/getPVsForThisAppliance"
        status, payload, body = fetch_json(url, self.timeout)
        if status >= 400 or not isinstance(payload, list):
            raise RuntimeError(
                f"registered-PV query failed: HTTP {status}: {body.strip()}"
            )
        if not all(isinstance(pv, str) for pv in payload):
            raise RuntimeError("registered-PV query returned an invalid payload")
        return sorted(set(payload))

    def type_info(self, pv: str) -> PolicyInfo | None:
        url = bpl_url(self.mgmt_url, "getPVTypeInfo", {"pv": pv})
        status, payload, body = fetch_json(url, self.timeout)
        if status == 404:
            return None
        if status >= 400 or not isinstance(payload, dict):
            raise RuntimeError(f"type-info query failed for {pv}: HTTP {status}: {body.strip()}")
        try:
            return PolicyInfo(
                str(payload["policyName"]),
                str(payload["samplingMethod"]).upper(),
                float(payload["samplingPeriod"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid type-info response for {pv}: {body.strip()}") from exc

    def change_archival_parameters(self, pv: str, policy: str) -> tuple[bool, str]:
        method, period = POLICY_SETTINGS[policy]
        url = bpl_url(
            self.mgmt_url,
            "changeArchivalParameters",
            {"pv": pv, "samplingmethod": method, "samplingperiod": str(period)},
        )
        status, body = fetch_text(url, self.timeout)
        return 200 <= status < 300, body.strip() or f"HTTP {status}"

    def submit(self, pv: str, policy: str) -> tuple[bool, str]:
        return archive_pv(
            self.mgmt_url,
            pv,
            policy,
            self.timeout,
            appliance_id=self.appliance_id,
        )

    def abort(self, pv: str) -> tuple[bool, str]:
        url = bpl_url(self.mgmt_url, "abortArchivingPV", {"pv": pv})
        status, body = fetch_text(url, self.timeout)
        return 200 <= status < 300, body.strip() or f"HTTP {status}"

    def pause(self, pv: str) -> tuple[bool, str]:
        url = bpl_url(self.mgmt_url, "pauseArchivingPV", {"pv": pv})
        status, body = fetch_text(url, self.timeout)
        return 200 <= status < 300, body.strip() or f"HTTP {status}"

    def resume(self, pv: str) -> tuple[bool, str]:
        url = bpl_url(self.mgmt_url, "resumeArchivingPV", {"pv": pv})
        status, body = fetch_text(url, self.timeout)
        return 200 <= status < 300, body.strip() or f"HTTP {status}"

    def retrieve(
        self, pv: str, minutes: float, *, from_time: str | None = None
    ) -> tuple[bool, str]:
        result = verify_retrieval(
            self.retrieval_url,
            pv,
            minutes=minutes,
            timeout=self.timeout,
            from_time=from_time,
        )
        return result.successful, f"{result.result}, samples={result.sample_count}"


class CatalogRepair:
    def __init__(
        self,
        client: ArchiverClient,
        pvs: Sequence[str],
        *,
        batch_size: int = 1,
        queue_timeout: float = 180.0,
        validation_timeout: float = 180.0,
        poll_interval: float = 2.0,
        retrieval_minutes: float = 10.0,
        retrieval_from: str | None = None,
        verify_new_sample: bool = False,
        default_policy: str = PHYSICAL_POLICY,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        output: Callable[[str], None] = print,
        verbose: bool = False,
    ) -> None:
        if batch_size != 1:
            raise ValueError("Archiver repair accepts individual PV submissions only")
        self.client = client
        self.pvs = list(pvs)
        self.batch_size = batch_size
        self.queue_timeout = queue_timeout
        self.validation_timeout = validation_timeout
        self.poll_interval = poll_interval
        self.retrieval_minutes = retrieval_minutes
        self.retrieval_from = retrieval_from
        self.verify_new_sample = verify_new_sample
        self.default_policy = default_policy
        self.clock = clock
        self.sleep = sleep
        self.output = output
        self.verbose = verbose
        self.out_of_scope_registered: list[str] = []
        self.last_workflows: list[Workflow] = []

    def detail(self, message: str) -> None:
        if self.verbose:
            self.output(message)

    def require_healthy(self) -> None:
        try:
            self.client.require_healthy()
        except InfrastructureFailure:
            raise
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            raise InfrastructureFailure(f"component health check failed: {exc}") from exc

    def audit(self) -> list[CatalogEntry]:
        try:
            workflows = self.client.workflows()
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            raise InfrastructureFailure(f"catalog workflow API unavailable: {exc}") from exc
        pending = {workflow.pv for workflow in workflows}
        self.last_workflows = workflows
        entries = []
        for pv in self.pvs:
            try:
                status = self.client.status(pv)
            except (OSError, RuntimeError, urllib.error.URLError) as exc:
                raise InfrastructureFailure(f"catalog status API unavailable for {pv}: {exc}") from exc
            if status.result == "endpoint failure":
                raise InfrastructureFailure(
                    f"catalog status API unavailable for {pv}: {status.status}"
                )
            category = classify_status(status, pending=pv in pending)
            policy = None
            diagnostic = None
            if category in {HEALTHY, REGISTERED_UNHEALTHY, PAUSED}:
                try:
                    policy = self.client.type_info(pv)
                except (OSError, RuntimeError, urllib.error.URLError) as exc:
                    category = UNVERIFIABLE
                    diagnostic = f"policy unverifiable: {exc}"
                else:
                    expected = policy_for_pv(pv, self.default_policy)
                    if policy is None:
                        category = UNVERIFIABLE
                        diagnostic = "registered PV has no type information"
                    elif not policy_is_correct(policy, expected):
                        category = REGISTERED_UNHEALTHY
                        diagnostic = (
                            f"wrong effective policy: {policy.name} "
                            f"{policy.sampling_method}/{policy.sampling_period:g}s; "
                            f"expected {expected}"
                        )
            entries.append(CatalogEntry(pv, category, status, policy, diagnostic))
        try:
            registered = set(self.client.registered_pvs())
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            raise InfrastructureFailure(
                f"registered-PV catalog API unavailable: {exc}"
            ) from exc
        self.out_of_scope_registered = sorted(registered.difference(self.pvs))
        return entries

    def print_summary(self, entries: Sequence[CatalogEntry], title: str) -> None:
        counts = Counter(entry.category for entry in entries)
        self.output(
            f"{title}: required total={len(entries)}, "
            + ", ".join(
                f"required {category}={counts[category]}" for category in CATEGORIES
            )
            + f", out-of-scope registered={len(self.out_of_scope_registered)}"
        )
        for category in CATEGORIES[1:]:
            names = [entry.pv for entry in entries if entry.category == category]
            if names:
                self.output(f"  {category}: {', '.join(names)}")
        if self.out_of_scope_registered:
            self.output(
                "  out-of-scope registered: "
                + ", ".join(self.out_of_scope_registered)
            )

    def pause_out_of_scope(self, pvs: Sequence[str]) -> list[str]:
        """Pause extra registrations without deleting type info or historical data."""
        paused: list[str] = []
        for pv in sorted(pvs):
            self.require_healthy()
            self.wait_for_idle(None, abort_on_timeout=False)
            category = classify_status(self.client.status(pv))
            if category == PAUSED:
                continue
            ok, message = self.client.pause(pv)
            if not ok:
                raise InfrastructureFailure(
                    f"could not pause out-of-scope PV {pv}: {message}"
                )
            deadline = self.clock() + self.validation_timeout
            while classify_status(self.client.status(pv)) != PAUSED:
                self.require_healthy()
                if self.clock() >= deadline:
                    raise InfrastructureFailure(
                        f"out-of-scope PV did not reach PAUSED state: {pv}"
                    )
                self.sleep(self.poll_interval)
            paused.append(pv)
            self.output(f"paused out-of-scope registration: {pv}")
        return paused

    def wait_for_idle(
        self,
        expected_pv: str | None,
        *,
        abort_on_timeout: bool,
    ) -> AttemptFailure | None:
        deadline = self.clock() + self.queue_timeout
        while True:
            self.require_healthy()
            try:
                workflows = self.client.workflows()
            except (OSError, RuntimeError, urllib.error.URLError) as exc:
                raise InfrastructureFailure(f"management queue API unavailable: {exc}") from exc
            if not workflows:
                return None
            unexpected = [item for item in workflows if item.pv != expected_pv]
            if expected_pv is not None and unexpected:
                details = ", ".join(f"{item.pv}:{item.state}" for item in unexpected)
                raise InfrastructureFailure(
                    f"unexpected overlapping management workflow while processing "
                    f"{expected_pv}: {details}"
                )
            if self.clock() >= deadline:
                details = ", ".join(f"{item.pv}:{item.state}" for item in workflows)
                if expected_pv is None or not abort_on_timeout:
                    raise InfrastructureFailure(
                        f"management queue could not return to idle: {details}"
                    )
                target = [item for item in workflows if item.pv == expected_pv]
                if not target:
                    raise InfrastructureFailure(
                        f"management queue serialization invariant failed: {details}"
                    )
                for workflow in target:
                    ok, message = self.client.abort(workflow.pv)
                    self.detail(
                        f"abort-stale {workflow.pv} state={workflow.state}: {message}"
                    )
                    if not ok:
                        raise InfrastructureFailure(
                            f"could not abort timed-out workflow for {expected_pv}: {message}"
                        )
                abort_deadline = self.clock() + self.queue_timeout
                while True:
                    self.require_healthy()
                    remaining = self.client.workflows()
                    if not remaining:
                        break
                    if any(item.pv != expected_pv for item in remaining):
                        extra = ", ".join(
                            f"{item.pv}:{item.state}" for item in remaining
                        )
                        raise InfrastructureFailure(
                            f"unexpected workflow appeared during cleanup: {extra}"
                        )
                    if self.clock() >= abort_deadline:
                        raise InfrastructureFailure(
                            f"management queue remained non-idle after aborting {expected_pv}"
                        )
                    self.sleep(self.poll_interval)
                states = {item.state.upper() for item in target}
                stage = (
                    "metadata gathering"
                    if "METAINFO_GATHERING" in states
                    else "management queue timeout"
                )
                return AttemptFailure(stage, f"workflow timed out: {details}")
            self.sleep(self.poll_interval)

    def cleanup_workflow(self, pv: str) -> None:
        workflows = self.client.workflows()
        unexpected = [item for item in workflows if item.pv != pv]
        if unexpected:
            details = ", ".join(f"{item.pv}:{item.state}" for item in unexpected)
            raise InfrastructureFailure(
                f"unexpected overlapping workflow during cleanup of {pv}: {details}"
            )
        for workflow in workflows:
            ok, message = self.client.abort(pv)
            self.detail(f"cleanup-abort {pv} state={workflow.state}: {message}")
            if not ok:
                raise InfrastructureFailure(
                    f"could not clear workflow for failed PV {pv}: {message}"
                )
        self.wait_for_idle(None, abort_on_timeout=False)

    def wait_for_healthy(self, pv: str) -> AttemptFailure | None:
        deadline = self.clock() + self.validation_timeout
        last_status: ArchiverStatus | None = None
        last_category = UNKNOWN
        while True:
            self.require_healthy()
            try:
                last_status = self.client.status(pv)
            except (OSError, RuntimeError, urllib.error.URLError) as exc:
                raise InfrastructureFailure(f"catalog status API unavailable for {pv}: {exc}") from exc
            last_category = classify_status(last_status)
            if last_category == HEALTHY:
                return None
            if self.clock() >= deadline:
                if last_status.status.strip().lower() == "being archived":
                    if last_status.connection_state is not True:
                        return AttemptFailure(
                            "connection timeout",
                            f"connection={last_status.connection_state}, "
                            f"last_event={last_status.last_event or 'Never'}",
                        )
                    if not has_real_last_event(last_status.last_event):
                        return AttemptFailure(
                            "first-event timeout",
                            "sampler connected but no real event was recorded",
                        )
                return AttemptFailure(
                    "unexpected status",
                    f"status={last_status.status}, category={last_category}",
                )
            self.sleep(self.poll_interval)

    def reactivate_one(self, pv: str) -> AttemptFailure | None:
        """Reactivate one registered unhealthy sampler through supported BPLs."""
        ok, message = self.client.pause(pv)
        self.detail(f"pause-for-reactivation {pv}: {message}")
        if not ok:
            return AttemptFailure("pause/resume retry", f"pause failed: {message}")

        deadline = self.clock() + self.validation_timeout
        while classify_status(self.client.status(pv)) != PAUSED:
            self.require_healthy()
            if self.clock() >= deadline:
                return AttemptFailure(
                    "pause/resume retry", "pause request did not reach PAUSED state"
                )
            self.sleep(self.poll_interval)

        ok, message = self.client.resume(pv)
        self.detail(f"resume-for-reactivation {pv}: {message}")
        if not ok:
            return AttemptFailure("pause/resume retry", f"resume failed: {message}")
        return None

    def verify_retrieval(self, pv: str) -> AttemptFailure | None:
        try:
            ok, message = self.client.retrieve(
                pv,
                self.retrieval_minutes,
                from_time=self.retrieval_from,
            )
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            raise InfrastructureFailure(f"retrieval infrastructure unavailable: {exc}") from exc
        self.detail(f"retrieval {'ok' if ok else 'failed'} {pv}: {message}")
        if not ok:
            if "endpoint failure" in message.lower():
                raise InfrastructureFailure(
                    f"retrieval infrastructure unavailable while checking {pv}: {message}"
                )
            return AttemptFailure("retrieval verification", message)
        return None

    def attempt_once(self, pv: str) -> tuple[str, AttemptFailure | None]:
        self.require_healthy()
        self.wait_for_idle(None, abort_on_timeout=False)
        try:
            category = classify_status(self.client.status(pv))
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            raise InfrastructureFailure(f"catalog status API unavailable for {pv}: {exc}") from exc
        action = "recovery"
        if category == HEALTHY:
            return "retrieval", self.verify_retrieval(pv)
        if category in {REGISTERED_UNHEALTHY, PAUSED}:
            failure = self.reactivate_one(pv)
            if failure:
                return action, failure
        elif category == NOT_ARCHIVED:
            action = "registration"
            policy = policy_for_pv(pv, self.default_policy)
            ok, message = self.client.submit(pv, policy)
            self.detail(f"submit {pv} policy={policy}: {message}")
            if not ok:
                return action, AttemptFailure("registration submission", message)
        else:
            return action, AttemptFailure(
                "unexpected status", f"cannot safely activate category={category}"
            )

        queue_failure = self.wait_for_idle(pv, abort_on_timeout=True)
        if queue_failure:
            return action, queue_failure
        health_failure = self.wait_for_healthy(pv)
        if health_failure:
            return action, health_failure
        return action, self.verify_retrieval(pv)

    def start_intervention(self, entry: CatalogEntry) -> tuple[str, AttemptFailure | None]:
        """Start one intervention without waiting for its asynchronous completion."""
        pv = entry.pv
        expected_policy = policy_for_pv(pv, self.default_policy)
        if entry.category == PENDING:
            return "wait for existing workflow", None
        if entry.category == NOT_ARCHIVED:
            ok, message = self.client.submit(pv, expected_policy)
            self.detail(f"submit {pv} policy={expected_policy}: {message}")
            if not ok:
                return "registration", AttemptFailure("registration submission", message)
            return "registration", None
        if entry.category == PAUSED:
            ok, message = self.client.resume(pv)
            self.detail(f"resume {pv}: {message}")
            if not ok:
                return "resume", AttemptFailure("pause/resume retry", message)
            return "resume", None
        if entry.category == REGISTERED_UNHEALTHY:
            if entry.policy is not None and not policy_is_correct(entry.policy, expected_policy):
                ok, message = self.client.change_archival_parameters(pv, expected_policy)
                self.detail(f"correct-policy {pv} policy={expected_policy}: {message}")
                if not ok:
                    return "policy correction", AttemptFailure("policy correction", message)
                return "policy correction", None
            ok, message = self.client.pause(pv)
            self.detail(f"pause-for-reactivation {pv}: {message}")
            if not ok:
                return "recovery", AttemptFailure("pause/resume retry", message)
            ok, message = self.client.resume(pv)
            self.detail(f"resume-for-reactivation {pv}: {message}")
            if not ok:
                return "recovery", AttemptFailure("pause/resume retry", message)
            return "recovery", None
        return "none", AttemptFailure(
            "unexpected status", entry.diagnostic or f"cannot safely activate {entry.category}"
        )

    def poll_globally(
        self,
        entries: Mapping[str, CatalogEntry],
        *,
        from_time: str | None,
    ) -> tuple[set[str], dict[str, AttemptFailure]]:
        """Verify all modified PVs with one shared deadline and one service check per cycle."""
        remaining = dict.fromkeys(entries)
        healthy: set[str] = set()
        last_failures: dict[str, AttemptFailure] = {}
        deadline = self.clock() + self.validation_timeout
        while remaining:
            self.require_healthy()
            try:
                workflows = self.client.workflows()
            except (OSError, RuntimeError, urllib.error.URLError) as exc:
                raise InfrastructureFailure(f"management queue API unavailable: {exc}") from exc
            unexpected = [item for item in workflows if item.pv not in entries]
            if unexpected:
                details = ", ".join(f"{item.pv}:{item.state}" for item in unexpected)
                raise InfrastructureFailure(f"unexpected overlapping management workflow: {details}")
            pending = {item.pv for item in workflows}

            for pv in list(remaining):
                try:
                    status = self.client.status(pv)
                except (OSError, RuntimeError, urllib.error.URLError) as exc:
                    raise InfrastructureFailure(f"catalog status API unavailable for {pv}: {exc}") from exc
                if status.result == "endpoint failure":
                    raise InfrastructureFailure(
                        f"catalog status API unavailable for {pv}: {status.status}"
                    )
                category = classify_status(status, pending=pv in pending)
                if category != HEALTHY:
                    if category == PENDING:
                        state = next((item.state for item in workflows if item.pv == pv), "pending")
                        last_failures[pv] = AttemptFailure("metadata gathering", state)
                    elif status.status.strip().lower() == "being archived" and status.connection_state is not True:
                        last_failures[pv] = AttemptFailure("connection timeout", "sampler is disconnected")
                    elif status.status.strip().lower() == "being archived":
                        last_failures[pv] = AttemptFailure("first-event timeout", "no valid archived event")
                    else:
                        last_failures[pv] = AttemptFailure("unexpected status", status.status)
                    continue
                try:
                    info = self.client.type_info(pv)
                except (OSError, RuntimeError, urllib.error.URLError) as exc:
                    raise InfrastructureFailure(f"catalog type-info API unavailable for {pv}: {exc}") from exc
                expected = policy_for_pv(pv, self.default_policy)
                if not policy_is_correct(info, expected):
                    last_failures[pv] = AttemptFailure("policy correction", "effective policy is still incorrect")
                    continue
                try:
                    ok, message = self.client.retrieve(
                        pv, self.retrieval_minutes, from_time=from_time
                    )
                except (OSError, RuntimeError, urllib.error.URLError) as exc:
                    raise InfrastructureFailure(f"retrieval infrastructure unavailable: {exc}") from exc
                if ok:
                    healthy.add(pv)
                    remaining.pop(pv)
                    last_failures.pop(pv, None)
                elif "endpoint failure" in message.lower():
                    raise InfrastructureFailure(
                        f"retrieval infrastructure unavailable while checking {pv}: {message}"
                    )
                else:
                    last_failures[pv] = AttemptFailure("retrieval verification", message)

            if not remaining or self.clock() >= deadline:
                break
            self.sleep(self.poll_interval)

        if remaining:
            workflows = self.client.workflows()
            unexpected = [item for item in workflows if item.pv not in entries]
            if unexpected:
                details = ", ".join(f"{item.pv}:{item.state}" for item in unexpected)
                raise InfrastructureFailure(f"unexpected workflow during timeout cleanup: {details}")
            for workflow in workflows:
                if workflow.pv in remaining:
                    ok, message = self.client.abort(workflow.pv)
                    self.detail(f"abort timed-out {workflow.pv}: {message}")
                    if not ok:
                        raise InfrastructureFailure(
                            f"could not clear timed-out workflow for {workflow.pv}: {message}"
                        )
            self.wait_for_idle(None, abort_on_timeout=False)
        return healthy, {pv: last_failures.get(pv, AttemptFailure("unexpected status", "verification timed out")) for pv in remaining}

    def print_final_summary(self, result: RepairResult) -> None:
        counts = Counter(entry.category for entry in result.final_entries)
        already = sum(item.outcome == "already healthy" for item in result.outcomes)
        recovered = sum(item.outcome.startswith("recovered") for item in result.outcomes)
        registered = sum(item.outcome.startswith("newly registered") for item in result.outcomes)
        failed = [item for item in result.outcomes if item.outcome == "failed"]
        self.output("Final repair summary:")
        values = (
            ("required total", len(self.pvs)),
            ("required healthy", counts[HEALTHY]),
            ("recovered during this run", recovered),
            ("newly registered during this run", registered),
            ("already healthy", already),
            ("required registered but unhealthy", counts[REGISTERED_UNHEALTHY]),
            ("required not being archived", counts[NOT_ARCHIVED]),
            ("required pending", counts[PENDING]),
            ("required paused", counts[PAUSED]),
            ("required unverifiable", counts[UNVERIFIABLE]),
            ("required unknown/error", counts[UNKNOWN]),
            ("out-of-scope registered", len(result.final_out_of_scope)),
            ("failed during this run", len(failed)),
        )
        self.output("  " + ", ".join(f"{name}={value}" for name, value in values))
        grouped: dict[tuple[str, str], list[str]] = {}
        for item in failed:
            key = (item.failure_stage or "unknown", item.diagnostic or "no diagnostic")
            grouped.setdefault(key, []).append(item.pv)
        for (stage, reason), pvs in grouped.items():
            self.output(f"  FAILED [{stage}] {reason}: {', '.join(pvs)}")
        if result.global_error:
            self.output(f"  GLOBAL FAILURE: {result.global_error}")

    def run_fail_fast(
        self,
        entries: Sequence[CatalogEntry],
        indices: Mapping[str, int],
        started: Mapping[str, str],
    ) -> list[PVOutcome]:
        """Retain the former serialized diagnostic mode."""
        outcomes: list[PVOutcome] = []
        for entry in entries:
            if entry.category == HEALTHY:
                outcomes.append(PVOutcome(
                    entry.pv, entry.category, "already healthy", 0, "none",
                    started[entry.pv], utc_now(), final_category=HEALTHY,
                ))
                continue
            action, failure = self.attempt_once(entry.pv)
            attempts = 1
            if failure:
                self.cleanup_workflow(entry.pv)
                action, failure = self.attempt_once(entry.pv)
                attempts = 2
            if failure:
                self.cleanup_workflow(entry.pv)
                outcomes.append(PVOutcome(
                    entry.pv, entry.category, "failed", attempts, action,
                    started[entry.pv], utc_now(), failure_stage=failure.stage,
                    diagnostic=failure.reason,
                ))
                self.output(
                    f"[{indices[entry.pv]}/{len(self.pvs)}] {entry.pv} FAILED: "
                    f"{failure.stage}: {failure.reason}; stopping"
                )
                break
            outcome = "newly registered" if entry.category == NOT_ARCHIVED else "recovered"
            if attempts > 1:
                outcome += " after retry"
            outcomes.append(PVOutcome(
                entry.pv, entry.category, outcome, attempts, action,
                started[entry.pv], utc_now(), final_category=HEALTHY,
            ))
        return outcomes

    def repair(
        self,
        repair_pvs: Iterable[str] = (),
        *,
        stop_on_first_failure: bool = False,
        pause_out_of_scope: bool = False,
    ) -> RepairResult:
        started_at = utc_now()
        outcomes: list[PVOutcome] = []
        initial: list[CatalogEntry] = []
        final: list[CatalogEntry] = []
        initial_out_of_scope: list[str] = []
        final_out_of_scope: list[str] = []
        paused_out_of_scope: list[str] = []
        global_error: str | None = None
        completed = False
        try:
            self.require_healthy()
            initial = self.audit()
            initial_out_of_scope = list(self.out_of_scope_registered)
            self.print_summary(initial, "Initial catalog audit")
            unexpected_initial = [
                item for item in self.last_workflows if item.pv not in set(self.pvs)
            ]
            if unexpected_initial:
                details = ", ".join(
                    f"{item.pv}:{item.state}" for item in unexpected_initial
                )
                raise InfrastructureFailure(
                    f"unexpected out-of-catalog management workflow: {details}"
                )
            if pause_out_of_scope:
                paused_out_of_scope = self.pause_out_of_scope(initial_out_of_scope)
            explicit = set(repair_pvs)
            invalid = explicit.difference(self.pvs)
            if invalid:
                raise InfrastructureFailure(
                    "explicit repair PVs are not configured: "
                    + ", ".join(sorted(invalid))
                )

            indices = {pv: index for index, pv in enumerate(self.pvs, start=1)}
            item_started = {entry.pv: utc_now() for entry in initial}
            parallel_entries: Sequence[CatalogEntry] = initial
            if stop_on_first_failure:
                outcomes.extend(
                    self.run_fail_fast(initial, indices, item_started)
                )
                parallel_entries = ()
            targets: dict[str, CatalogEntry] = {}
            actions: dict[str, str] = {}
            attempt_counts: Counter[str] = Counter()
            failures: dict[str, AttemptFailure] = {}

            for entry in parallel_entries:
                if entry.category == HEALTHY and not self.verify_new_sample:
                    outcomes.append(PVOutcome(
                        entry.pv, entry.category, "already healthy", 0, "none",
                        item_started[entry.pv], utc_now(), final_category=HEALTHY,
                    ))
                    self.output(
                        f"[{indices[entry.pv]}/{len(self.pvs)}] "
                        f"{entry.pv} already healthy"
                    )
                else:
                    targets[entry.pv] = entry

            # Phase 2: start every intervention before any long per-PV wait.
            for pv, entry in targets.items():
                if entry.category == HEALTHY:
                    actions[pv] = "verify new sample"
                    continue
                action, failure = self.start_intervention(entry)
                actions[pv] = action
                attempt_counts[pv] = 0 if entry.category == PENDING else 1
                if failure:
                    failures[pv] = failure
                else:
                    self.output(
                        f"[{indices[pv]}/{len(self.pvs)}] {pv} {action} started"
                    )

            verification = {
                pv: entry for pv, entry in targets.items() if pv not in failures
            }
            retrieval_from = started_at if self.verify_new_sample else self.retrieval_from
            if verification:
                _healthy, timed_out = self.poll_globally(
                    verification, from_time=retrieval_from
                )
                failures.update(timed_out)

            # Retry all isolated failures as one second wave, never one timeout per PV.
            if failures:
                self.require_healthy()
                retry_entries = {entry.pv: entry for entry in self.audit()}
                retry_verification: dict[str, CatalogEntry] = {}
                for pv in list(failures):
                    entry = retry_entries[pv]
                    if entry.category == HEALTHY:
                        retry_verification[pv] = entry
                        attempt_counts[pv] += 1
                        failures.pop(pv, None)
                        continue
                    action, failure = self.start_intervention(entry)
                    actions[pv] = action
                    attempt_counts[pv] += 1
                    if failure:
                        failures[pv] = failure
                    else:
                        failures.pop(pv, None)
                        retry_verification[pv] = entry
                        self.detail(f"retry started {pv}: {action}")
                if retry_verification:
                    _healthy, timed_out = self.poll_globally(
                        retry_verification, from_time=retrieval_from
                    )
                    failures.update(timed_out)

            for pv, entry in targets.items():
                failure = failures.get(pv)
                if failure:
                    outcomes.append(PVOutcome(
                        pv, entry.category, "failed", attempt_counts[pv],
                        actions.get(pv, "none"), item_started[pv], utc_now(),
                        failure_stage=failure.stage, diagnostic=failure.reason,
                    ))
                    self.output(
                        f"[{indices[pv]}/{len(self.pvs)}] {pv} FAILED: "
                        f"{failure.stage}: {failure.reason}"
                    )
                else:
                    outcome = (
                        "newly registered" if entry.category == NOT_ARCHIVED else "recovered"
                    )
                    if attempt_counts[pv] > 1:
                        outcome += " after retry"
                    outcomes.append(PVOutcome(
                        pv, entry.category, outcome, attempt_counts[pv],
                        actions.get(pv, "verification"), item_started[pv], utc_now(),
                        final_category=HEALTHY,
                    ))
                    self.output(f"[{indices[pv]}/{len(self.pvs)}] {pv} {outcome}")

            attempted = {item.pv for item in outcomes}
            for entry in initial:
                if entry.pv not in attempted:
                    outcomes.append(PVOutcome(
                        entry.pv, entry.category, "not attempted", 0, "none",
                        item_started[entry.pv], utc_now(),
                        diagnostic="stop-on-first-failure",
                    ))

            self.require_healthy()
            self.wait_for_idle(None, abort_on_timeout=False)
            final = self.audit()
            final_out_of_scope = list(self.out_of_scope_registered)
            workflows = self.client.workflows()
            if workflows:
                details = ", ".join(f"{item.pv}:{item.state}" for item in workflows)
                raise InfrastructureFailure(
                    f"management queue was not idle before final validation: {details}"
                )
            final_by_pv = {entry.pv: entry.category for entry in final}
            for outcome in outcomes:
                outcome.final_category = final_by_pv.get(outcome.pv)
            completed = True
        except InfrastructureFailure as exc:
            global_error = str(exc)
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            global_error = f"infrastructure failure: {exc}"

        result = RepairResult(
            started_at,
            utc_now(),
            outcomes,
            initial,
            final,
            initial_out_of_scope,
            final_out_of_scope,
            paused_out_of_scope,
            completed,
            global_error,
        )
        self.print_final_summary(result)
        return result


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def ready_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def report_payload(result: RepairResult, configured: Sequence[str]) -> dict[str, Any]:
    initial_counts = Counter(entry.category for entry in result.initial_entries)
    final_counts = Counter(entry.category for entry in result.final_entries)
    failed = [item for item in result.outcomes if item.outcome == "failed"]
    return {
        "schema_version": 3,
        "started_at": result.started_at,
        "completed_at": result.completed_at,
        "completed": result.completed,
        "fully_healthy": result.fully_healthy,
        "global_error": result.global_error,
        "summary": {
            "required_total": len(configured),
            "required_healthy": final_counts[HEALTHY],
            "recovered_during_run": sum(
                item.outcome.startswith("recovered") for item in result.outcomes
            ),
            "newly_registered_during_run": sum(
                item.outcome.startswith("newly registered") for item in result.outcomes
            ),
            "already_healthy": sum(
                item.outcome == "already healthy" for item in result.outcomes
            ),
            "registered_but_unhealthy": final_counts[REGISTERED_UNHEALTHY],
            "not_being_archived": final_counts[NOT_ARCHIVED],
            "pending": final_counts[PENDING],
            "paused": final_counts[PAUSED],
            "unverifiable": final_counts[UNVERIFIABLE],
            "unknown_error": final_counts[UNKNOWN],
            "failed_during_run": len(failed),
            "out_of_scope_registered": len(result.final_out_of_scope),
        },
        "initial_catalog": {category: initial_counts[category] for category in CATEGORIES},
        "final_catalog": {category: final_counts[category] for category in CATEGORIES},
        "failed_pvs": [
            {
                "pv": item.pv,
                "stage": item.failure_stage,
                "reason": item.diagnostic,
            }
            for item in failed
        ],
        "initial_out_of_scope_registered": result.initial_out_of_scope,
        "final_out_of_scope_registered": result.final_out_of_scope,
        "paused_out_of_scope_during_run": result.paused_out_of_scope,
        "pv_outcomes": [item.as_dict() for item in result.outcomes],
    }


def write_report(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pv_lists", nargs="+", type=Path)
    parser.add_argument("--mgmt-url", default=os.environ.get("BDX_ARCHIVER_MGMT_URL", DEFAULT_MGMT_URL))
    parser.add_argument(
        "--retrieval-url",
        default=os.environ.get("BDX_ARCHIVER_RETRIEVAL_DATA_URL", DEFAULT_RETRIEVAL_URL),
    )
    parser.add_argument("--batch-size", type=positive_int, default=1)
    parser.add_argument("--queue-timeout", type=non_negative_float, default=180.0)
    parser.add_argument(
        "--validation-timeout",
        "--timeout",
        dest="validation_timeout",
        type=non_negative_float,
        default=180.0,
        help="Global verification timeout per intervention wave (default: 180 s).",
    )
    parser.add_argument("--poll-interval", type=non_negative_float, default=2.0)
    parser.add_argument("--http-timeout", type=non_negative_float, default=10.0)
    parser.add_argument(
        "--appliance",
        default=os.environ.get("BDX_ARCHIVER_APPLIANCE_ID"),
        help=(
            "Single-appliance identity. When set, the supported local-appliance "
            "request path skips redundant cluster capacity planning."
        ),
    )
    parser.add_argument("--retrieval-minutes", type=non_negative_float, default=10.0)
    parser.add_argument(
        "--retrieval-from",
        help="Require representative samples at or after this ISO-8601 timestamp.",
    )
    parser.add_argument(
        "--verify-new-sample",
        action="store_true",
        help="Require a sample newer than this repair run, including for healthy PVs.",
    )
    parser.add_argument("--repair-pv", action="append", default=[])
    parser.add_argument(
        "--stop-on-first-failure",
        action="store_true",
        help="Stop cleanly after the first persistent isolated PV failure.",
    )
    parser.add_argument(
        "--pause-out-of-scope",
        action="store_true",
        help=(
            "Pause registered PVs outside the configured target without deleting "
            "type information or historical samples."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print registration, polling-retry, and retrieval details.",
    )
    parser.add_argument("--report-path", type=Path)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path(os.environ.get("BDX_ARCHIVER_STATE_DIR", ".")) / "run",
    )
    parser.add_argument(
        "--engine-url",
        default=os.environ.get(
            "BDX_ARCHIVER_ENGINE_URL", "http://127.0.0.1:17666/engine/bpl"
        ),
    )
    parser.add_argument(
        "--etl-url",
        default=os.environ.get(
            "BDX_ARCHIVER_ETL_URL", "http://127.0.0.1:17667/etl/bpl"
        ),
    )
    parser.add_argument(
        "--retrieval-bpl-url",
        default=os.environ.get(
            "BDX_ARCHIVER_RETRIEVAL_BPL_URL",
            "http://127.0.0.1:17668/retrieval/bpl",
        ),
    )
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Classify the complete catalog without registration or abort requests.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.batch_size != 1:
        print("Archiver repair requires --batch-size 1 (individual submissions).", file=sys.stderr)
        return 2
    try:
        pvs = read_pv_list(args.pv_lists)
    except OSError as exc:
        print(f"Catalog could not be loaded: {exc}", file=sys.stderr)
        return 2
    if not pvs:
        print("No configured PVs found.", file=sys.stderr)
        return 2
    ready_urls = {
        "management": ready_url(
            args.mgmt_url, os.environ.get("BDX_ARCHIVER_MGMT_READY_PATH", "getVersions")
        ),
        "engine": ready_url(
            args.engine_url,
            os.environ.get("BDX_ARCHIVER_ENGINE_READY_PATH", "getVersion"),
        ),
        "etl": ready_url(
            args.etl_url, os.environ.get("BDX_ARCHIVER_ETL_READY_PATH", "getVersion")
        ),
        "retrieval": ready_url(
            args.retrieval_bpl_url,
            os.environ.get("BDX_ARCHIVER_RETRIEVAL_READY_PATH", "getVersion"),
        ),
    }
    client = ArchiverClient(
        args.mgmt_url,
        args.retrieval_url,
        args.http_timeout,
        args.appliance,
        ready_urls,
    )
    repair = CatalogRepair(
        client,
        pvs,
        batch_size=args.batch_size,
        queue_timeout=args.queue_timeout,
        validation_timeout=args.validation_timeout,
        poll_interval=args.poll_interval,
        retrieval_minutes=args.retrieval_minutes,
        retrieval_from=args.retrieval_from,
        verify_new_sample=args.verify_new_sample,
        verbose=args.verbose,
    )
    try:
        if args.audit_only:
            repair.require_healthy()
            workflows = client.workflows()
            entries = repair.audit()
            repair.print_summary(entries, "Catalog audit")
            if workflows:
                print(
                    "Pending workflows: "
                    + ", ".join(f"{item.pv}:{item.state}" for item in workflows)
                )
            return 0 if not workflows and all(
                entry.category == HEALTHY for entry in entries
            ) else 1
        result = repair.repair(
            args.repair_pv,
            stop_on_first_failure=args.stop_on_first_failure,
            pause_out_of_scope=args.pause_out_of_scope,
        )
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        report_path = args.report_path or args.report_dir / f"archiver-repair-{timestamp}.json"
        try:
            write_report(report_path, report_payload(result, pvs))
        except OSError as exc:
            print(f"Could not write Archiver repair report: {exc}", file=sys.stderr)
            return 2
        print(f"JSON report: {report_path}")
        if result.global_error or not result.completed:
            return 2
        if not result.fully_healthy:
            return 1
        retrieval_failures = []
        for pv in DEFAULT_REPRESENTATIVES:
            if pv not in pvs:
                print(f"Representative retrieval PV is not configured: {pv}", file=sys.stderr)
                retrieval_failures.append(pv)
                continue
            ok, message = client.retrieve(
                pv, args.retrieval_minutes, from_time=args.retrieval_from
            )
            print(f"retrieval {'ok' if ok else 'failed'} {pv}: {message}")
            if not ok:
                retrieval_failures.append(pv)
        if retrieval_failures:
            print("Retrieval failures: " + ", ".join(retrieval_failures), file=sys.stderr)
            return 1
    except InfrastructureFailure as exc:
        print(f"Archiver audit/repair infrastructure failure: {exc}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        print(f"Archiver audit/repair failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
