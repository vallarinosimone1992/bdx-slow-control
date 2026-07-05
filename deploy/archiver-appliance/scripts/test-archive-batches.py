#!/usr/bin/env python3
"""Batch-test BDX Archiver Appliance PV registration and retrieval."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from archiver_common import (
    DEFAULT_MGMT_URL,
    DEFAULT_RETRIEVAL_URL,
    ArchiverStatus,
    RetrievalResult,
    archive_pv,
    category_for_pv,
    get_pv_status,
    iso_utc,
    policy_for_pv,
    read_pv_list,
    subsystem_for_pv,
    verify_retrieval,
)


DEFAULT_CONFIG_DIR = Path("config/profiles/prototype")
DEFAULT_PV_LIST = Path("deploy/archiver-appliance/pv-lists/prototype.txt")
DEFAULT_IOC_LOG = Path("/tmp/bdx-ioc-python313.log")
DEFAULT_OUTPUT_ROOT = Path("/tmp/bdx-archiver-batch-tests")

PROTOCOL_ERROR_PATTERNS = (
    "Unrecognized subscriptionid",
    "Unknown Channel sid",
    "RemoteProtocolError",
)

BATCH_ORDER = (
    ("chiller", "physical"),
    ("chiller", "state"),
    ("chiller", "diagnostic"),
    ("psu", "physical"),
    ("psu", "state"),
    ("psu", "diagnostic"),
    ("environment", "physical"),
    ("environment", "state"),
    ("environment", "diagnostic"),
)

CSV_FIELDS = (
    "batch",
    "pv",
    "subsystem",
    "category",
    "policy",
    "registration_result",
    "archiver_status",
    "connectionState",
    "lastEvent",
    "connectionLossRegainCount",
    "retrieval_result",
    "retrieved_sample_count",
    "new_protocol_error_count",
    "healthy",
    "failure_reason",
)


@dataclass(frozen=True)
class Batch:
    index: int
    subsystem: str
    category: str
    pvs: list[str]

    @property
    def name(self) -> str:
        return f"batch-{self.index:03d}-{self.subsystem}-{self.category}"


@dataclass(frozen=True)
class BatchOutcome:
    rows: list[dict[str, object]]
    protocol_error_count: int
    failed: bool


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--pv-list", action="append", type=Path, dest="pv_lists")
    parser.add_argument(
        "--subsystem",
        choices=("all", "environment", "psu", "chiller"),
        default="all",
    )
    parser.add_argument(
        "--category",
        choices=("all", "physical", "state", "diagnostic"),
        default="all",
    )
    parser.add_argument("--batch-size", type=positive_int, default=5)
    parser.add_argument("--wait-seconds", type=nonnegative_float, default=75.0)
    parser.add_argument("--retrieval-minutes", type=nonnegative_float, default=20.0)
    parser.add_argument("--mgmt-url", default=DEFAULT_MGMT_URL)
    parser.add_argument("--retrieval-url", default=DEFAULT_RETRIEVAL_URL)
    parser.add_argument("--ioc-log", type=Path, default=DEFAULT_IOC_LOG)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--max-new-protocol-errors", type=int, default=0)
    parser.add_argument("--continue-on-protocol-errors", action="store_true")
    parser.add_argument("--json-summary", type=Path)
    parser.add_argument("--csv-summary", type=Path)
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser.parse_args(argv)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def parse_bdx_pv_list_output(text: str) -> list[str]:
    return ordered_unique([line.strip() for line in text.splitlines() if line.strip()])


def find_bdx_pv_list_command() -> str:
    command = shutil.which("bdx-pv-list")
    if command:
        return command
    sibling = Path(sys.executable).parent / "bdx-pv-list"
    if sibling.exists():
        return str(sibling)
    raise RuntimeError(
        "bdx-pv-list was not found. Activate the BDX Python environment or install the package."
    )


def generate_ioc_pvs(config_dir: Path) -> list[str]:
    command = [find_bdx_pv_list_command(), "--config-dir", str(config_dir)]
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    return parse_bdx_pv_list_output(result.stdout)


def split_present_missing(requested_pvs: list[str], ioc_pvs: list[str]) -> tuple[list[str], list[str]]:
    ioc_set = set(ioc_pvs)
    present: list[str] = []
    missing: list[str] = []
    for pv in requested_pvs:
        if pv in ioc_set:
            present.append(pv)
        else:
            missing.append(pv)
    return present, missing


def group_pvs(
    pvs: list[str],
    *,
    subsystem_filter: str = "all",
    category_filter: str = "all",
) -> dict[tuple[str, str], list[str]]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for pv in pvs:
        subsystem = subsystem_for_pv(pv)
        category = category_for_pv(pv)
        if subsystem_filter != "all" and subsystem != subsystem_filter:
            continue
        if category_filter != "all" and category != category_filter:
            continue
        grouped.setdefault((subsystem, category), []).append(pv)
    return grouped


def ordered_group_keys(grouped: dict[tuple[str, str], list[str]]) -> list[tuple[str, str]]:
    ordered = [key for key in BATCH_ORDER if key in grouped]
    extras = sorted(key for key in grouped if key not in BATCH_ORDER)
    return ordered + extras


def build_batches(grouped: dict[tuple[str, str], list[str]], batch_size: int) -> list[Batch]:
    batches: list[Batch] = []
    for subsystem, category in ordered_group_keys(grouped):
        pvs = grouped[(subsystem, category)]
        for start in range(0, len(pvs), batch_size):
            batches.append(
                Batch(
                    index=len(batches) + 1,
                    subsystem=subsystem,
                    category=category,
                    pvs=pvs[start : start + batch_size],
                )
            )
    return batches


def write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def log_offset(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def read_new_log(path: Path, offset: int) -> str:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return ""
    start = offset if offset <= size else 0
    with path.open("rb") as stream:
        stream.seek(start)
        return stream.read().decode("utf-8", errors="replace")


def protocol_error_lines(text: str) -> list[str]:
    return [
        line
        for line in text.splitlines()
        if any(pattern in line for pattern in PROTOCOL_ERROR_PATTERNS)
    ]


def count_protocol_errors(text: str) -> int:
    return len(protocol_error_lines(text))


def status_failure_reason(status: ArchiverStatus, retrieval: RetrievalResult) -> str:
    if status.result == "endpoint failure":
        return "endpoint failure"
    if status.result == "present but not registered" or status.status == "Not registered":
        return "present but not registered"
    if status.status != "Being archived":
        return f"archiver status is {status.status}"
    if status.connection_state is False:
        return "disconnected"
    if status.connection_state is not True:
        return "connection state unavailable"
    if not status.last_event or status.last_event == "Never":
        return "initial sampling incomplete"
    if retrieval.result.startswith("endpoint failure"):
        return retrieval.result
    if retrieval.result == "unknown PV":
        return "unknown PV"
    if retrieval.result == "known PV without samples" or retrieval.sample_count <= 0:
        return "archived but no samples"
    return ""


def pv_is_healthy(status: ArchiverStatus, retrieval: RetrievalResult) -> bool:
    return status_failure_reason(status, retrieval) == ""


def safe_get_status(
    status_fn: Callable[[str, str, float], ArchiverStatus],
    mgmt_url: str,
    pv: str,
    timeout: float,
) -> ArchiverStatus:
    try:
        return status_fn(mgmt_url, pv, timeout)
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        return ArchiverStatus("endpoint failure", "Endpoint failure", raw=str(exc))


def safe_verify_retrieval(
    retrieval_fn: Callable[..., RetrievalResult],
    retrieval_url: str,
    pv: str,
    retrieval_minutes: float,
    timeout: float,
    from_time: str,
    to_time: str,
) -> RetrievalResult:
    try:
        return retrieval_fn(
            retrieval_url,
            pv,
            minutes=retrieval_minutes,
            timeout=timeout,
            from_time=from_time,
            to_time=to_time,
        )
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        return RetrievalResult(f"endpoint failure: {exc}", 0, False)


def registration_action(
    *,
    pv: str,
    policy: str,
    pre_status: ArchiverStatus,
    register: bool,
    mgmt_url: str,
    timeout: float,
    archive_fn: Callable[[str, str, str, float], tuple[bool, str]],
) -> str:
    if pre_status.already_registered:
        return "already-registered"
    if not register:
        return "dry-run-not-registered"
    if pre_status.result == "endpoint failure":
        return f"status-check-failed: {pre_status.raw or pre_status.status}"

    try:
        ok, message = archive_fn(mgmt_url, pv, policy, timeout)
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        return f"registration rejected: {exc}"
    if ok:
        return f"submitted: {message}"
    return f"registration rejected: {message}"


def run_batch(
    batch: Batch,
    *,
    output_dir: Path,
    mgmt_url: str,
    retrieval_url: str,
    ioc_log: Path,
    register: bool,
    wait_seconds: float,
    retrieval_minutes: float,
    timeout: float,
    max_new_protocol_errors: int,
    continue_on_protocol_errors: bool,
    status_fn: Callable[[str, str, float], ArchiverStatus] = get_pv_status,
    archive_fn: Callable[[str, str, str, float], tuple[bool, str]] = archive_pv,
    retrieval_fn: Callable[..., RetrievalResult] = verify_retrieval,
) -> BatchOutcome:
    batch_file = output_dir / f"{batch.name}.txt"
    registration_log = output_dir / f"{batch.name}-registration.log"
    caproto_log = output_dir / f"{batch.name}-caproto-errors.log"

    write_lines(batch_file, batch.pvs)
    start_offset = log_offset(ioc_log)

    log_lines: list[str] = []
    registration_results: dict[str, str] = {}
    for pv in batch.pvs:
        policy = policy_for_pv(pv)
        pre_status = safe_get_status(status_fn, mgmt_url, pv, timeout)
        result = registration_action(
            pv=pv,
            policy=policy,
            pre_status=pre_status,
            register=register,
            mgmt_url=mgmt_url,
            timeout=timeout,
            archive_fn=archive_fn,
        )
        registration_results[pv] = result
        mode = "register" if register else "dry-run"
        log_lines.append(f"{mode} {pv} policy={policy} result={result}")

    write_lines(registration_log, log_lines)

    if wait_seconds:
        time.sleep(wait_seconds)

    now = datetime.now(timezone.utc)
    from_time = iso_utc(now - timedelta(minutes=retrieval_minutes))
    to_time = iso_utc(now)
    new_log_text = read_new_log(ioc_log, start_offset)
    error_lines = protocol_error_lines(new_log_text)
    write_lines(caproto_log, error_lines)
    protocol_error_count = len(error_lines)

    rows: list[dict[str, object]] = []
    for pv in batch.pvs:
        status = safe_get_status(status_fn, mgmt_url, pv, timeout)
        retrieval = safe_verify_retrieval(
            retrieval_fn,
            retrieval_url,
            pv,
            retrieval_minutes,
            timeout,
            from_time,
            to_time,
        )
        failure_reason = status_failure_reason(status, retrieval)
        rows.append(
            {
                "batch": batch.name,
                "pv": pv,
                "subsystem": subsystem_for_pv(pv),
                "category": category_for_pv(pv),
                "policy": policy_for_pv(pv),
                "registration_result": registration_results[pv],
                "archiver_status": status.status,
                "connectionState": status.connection_state,
                "lastEvent": status.last_event,
                "connectionLossRegainCount": status.connection_loss_regain_count,
                "retrieval_result": retrieval.result,
                "retrieved_sample_count": retrieval.sample_count,
                "new_protocol_error_count": protocol_error_count,
                "healthy": not failure_reason,
                "failure_reason": failure_reason,
            }
        )

    protocol_failure = (
        protocol_error_count > max_new_protocol_errors and not continue_on_protocol_errors
    )
    failed = protocol_failure or any(not row["healthy"] for row in rows)
    return BatchOutcome(rows=rows, protocol_error_count=protocol_error_count, failed=failed)


def summarize(
    *,
    ioc_pvs: list[str],
    requested_pvs: list[str],
    present_pvs: list[str],
    missing_pvs: list[str],
    rows: list[dict[str, object]],
    total_protocol_errors: int,
) -> dict[str, object]:
    grouped: dict[str, dict[str, int]] = {}
    for row in rows:
        key = f"{row['subsystem']}:{row['category']}"
        group = grouped.setdefault(key, {"tested": 0, "successful": 0, "failed": 0})
        group["tested"] += 1
        if row["healthy"]:
            group["successful"] += 1
        else:
            group["failed"] += 1

    successful = sum(1 for row in rows if row["healthy"])
    failed = len(rows) - successful
    return {
        "ioc_count": len(ioc_pvs),
        "requested_count": len(requested_pvs),
        "present_count": len(present_pvs),
        "missing_count": len(missing_pvs),
        "tested_count": len(rows),
        "successful_count": successful,
        "failed_count": failed,
        "total_new_protocol_errors": total_protocol_errors,
        "grouped": grouped,
        "missing_pvs": missing_pvs,
        "results": rows,
        "note": (
            "Absent prototype PVs do not imply that the production archive lists are wrong. "
            "The prototype profile may intentionally define fewer simulated hardware channels."
        ),
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


def final_report_text(summary: dict[str, object]) -> str:
    lines = [
        "BDX Archiver batch validation report",
        "",
        f"Total IOC PVs: {summary['ioc_count']}",
        f"Requested PVs: {summary['requested_count']}",
        f"Present requested PVs: {summary['present_count']}",
        f"Missing requested PVs: {summary['missing_count']}",
        f"Tested PVs: {summary['tested_count']}",
        f"Successful PVs: {summary['successful_count']}",
        f"Failed PVs: {summary['failed_count']}",
        f"Total new protocol errors: {summary['total_new_protocol_errors']}",
        "",
        "Grouped results:",
    ]
    grouped = summary["grouped"]
    if isinstance(grouped, dict):
        for key in sorted(grouped):
            value = grouped[key]
            if isinstance(value, dict):
                lines.append(
                    f"  {key}: tested={value['tested']} "
                    f"successful={value['successful']} failed={value['failed']}"
                )
    lines.extend(["", str(summary["note"])])
    return "\n".join(lines) + "\n"


def write_summary_files(
    output_dir: Path,
    summary: dict[str, object],
    rows: list[dict[str, object]],
    *,
    json_summary: Path | None = None,
    csv_summary: Path | None = None,
) -> None:
    summary_json = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    (output_dir / "summary.json").write_text(summary_json, encoding="utf-8")
    write_csv(output_dir / "summary.csv", rows)
    (output_dir / "final-report.txt").write_text(final_report_text(summary), encoding="utf-8")

    if json_summary is not None:
        json_summary.write_text(summary_json, encoding="utf-8")
    if csv_summary is not None:
        write_csv(csv_summary, rows)


def print_batch_table(batch: Batch, outcome: BatchOutcome) -> None:
    print(
        f"{batch.name}: pv={len(batch.pvs)} "
        f"ok={sum(1 for row in outcome.rows if row['healthy'])} "
        f"failed={sum(1 for row in outcome.rows if not row['healthy'])} "
        f"protocol_errors={outcome.protocol_error_count}"
    )
    for row in outcome.rows:
        status = "OK" if row["healthy"] else "FAIL"
        print(
            f"  {status:4} {row['pv']} status={row['archiver_status']} "
            f"retrieval={row['retrieval_result']}"
        )


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return DEFAULT_OUTPUT_ROOT / stamp


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pv_lists = args.pv_lists or [DEFAULT_PV_LIST]
    output_dir = args.output_dir or default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    requested_pvs = read_pv_list(pv_lists)
    ioc_pvs = generate_ioc_pvs(args.config_dir)
    present_pvs, missing_pvs = split_present_missing(requested_pvs, ioc_pvs)

    write_lines(output_dir / "ioc-pvs.txt", ioc_pvs)
    write_lines(output_dir / "requested-pvs.txt", requested_pvs)
    write_lines(output_dir / "present-pvs.txt", present_pvs)
    write_lines(output_dir / "missing-pvs.txt", missing_pvs)

    grouped = group_pvs(
        present_pvs,
        subsystem_filter=args.subsystem,
        category_filter=args.category,
    )
    batches = build_batches(grouped, args.batch_size)

    print(f"Output directory: {output_dir}")
    print(f"IOC PVs: {len(ioc_pvs)}")
    print(f"Requested PVs: {len(requested_pvs)}")
    print(f"Present requested PVs: {len(present_pvs)}")
    print(f"Missing requested PVs: {len(missing_pvs)}")
    print("Missing prototype PVs are reported only; repository PV-list files are not modified.")
    print(f"Selected batches: {len(batches)}")
    if not args.register:
        print("Mode: dry-run, archivePV will not be called.")

    all_rows: list[dict[str, object]] = []
    total_protocol_errors = 0
    stopped_early = False
    for batch in batches:
        outcome = run_batch(
            batch,
            output_dir=output_dir,
            mgmt_url=args.mgmt_url,
            retrieval_url=args.retrieval_url,
            ioc_log=args.ioc_log,
            register=args.register,
            wait_seconds=args.wait_seconds,
            retrieval_minutes=args.retrieval_minutes,
            timeout=args.timeout,
            max_new_protocol_errors=args.max_new_protocol_errors,
            continue_on_protocol_errors=args.continue_on_protocol_errors,
        )
        all_rows.extend(outcome.rows)
        total_protocol_errors += outcome.protocol_error_count
        print_batch_table(batch, outcome)
        if args.stop_on_failure and outcome.failed:
            stopped_early = True
            print("Stopping before the next batch because the current batch failed.")
            break

    summary = summarize(
        ioc_pvs=ioc_pvs,
        requested_pvs=requested_pvs,
        present_pvs=present_pvs,
        missing_pvs=missing_pvs,
        rows=all_rows,
        total_protocol_errors=total_protocol_errors,
    )
    summary["stopped_early"] = stopped_early
    write_summary_files(
        output_dir,
        summary,
        all_rows,
        json_summary=args.json_summary,
        csv_summary=args.csv_summary,
    )

    print((output_dir / "final-report.txt").read_text(encoding="utf-8"))
    if summary["failed_count"] or (
        total_protocol_errors > args.max_new_protocol_errors
        and not args.continue_on_protocol_errors
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
