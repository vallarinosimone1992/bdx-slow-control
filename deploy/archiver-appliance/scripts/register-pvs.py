#!/usr/bin/env python3
"""Register BDX PVs with the EPICS Archiver Appliance management BPL API."""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_MGMT_URL = "http://127.0.0.1:17665/mgmt/bpl"


def read_pv_list(paths: list[Path]) -> list[str]:
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


def policy_for_pv(pv: str, default_policy: str) -> str:
    state_suffixes = (
        ":COMM_OK",
        ":OUTPUT_RBV",
        ":RUN_RBV",
        ":FAULT",
        ":DEVIATION_WARNING",
        ":DEVIATION_ALARM",
        ":STATUS_OK",
        ":ALL_OUTPUTS_OFF",
    )
    diagnostic_suffixes = (
        ":COMM_STATUS",
        ":ERROR_MESSAGE",
        ":OUTPUT_STATE",
        ":RUN_STATE",
        ":DEVIATION_STATUS",
        ":STATUS",
        ":DEVICE_STATUS",
        ":FAULT_DIAGNOSIS",
    )
    if pv.endswith(":HEARTBEAT"):
        return "BDX_Heartbeat_Slow"
    if pv.endswith(state_suffixes):
        return "BDX_State_Change"
    if pv.endswith(diagnostic_suffixes):
        return "BDX_Diagnostic_Change"
    return default_policy


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


def pv_already_registered(mgmt_url: str, pv: str, timeout: float) -> bool:
    status_url = bpl_url(mgmt_url, "getPVStatus", {"pv": pv})
    status, body = fetch_text(status_url, timeout)
    if status >= 500:
        raise RuntimeError(f"management endpoint failed for {pv}: HTTP {status}")
    if status == 404:
        return False
    lowered = body.lower()
    if "not being archived" in lowered or "not found" in lowered or "unknown" in lowered:
        return False
    return status < 400 and bool(body.strip())


def archive_pv(mgmt_url: str, pv: str, policy: str, timeout: float) -> tuple[bool, str]:
    archive_url = bpl_url(mgmt_url, "archivePV", {"pv": pv, "policy": policy})
    status, body = fetch_text(archive_url, timeout)
    if 200 <= status < 300:
        return True, body.strip() or "submitted"
    return False, f"HTTP {status}: {body.strip()}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pv_lists", nargs="+", type=Path, help="PV-list files to register.")
    parser.add_argument(
        "--mgmt-url",
        default=os.environ.get("BDX_ARCHIVER_MGMT_URL", DEFAULT_MGMT_URL),
        help="Archiver Appliance management BPL base URL.",
    )
    parser.add_argument(
        "--policy",
        default=os.environ.get("BDX_ARCHIVER_DEFAULT_POLICY", "BDX_Physical_5s"),
        help="Default policy name for physical readbacks.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without API calls.")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pvs = read_pv_list(args.pv_lists)
    if not pvs:
        print("No PVs found in the provided PV-list files.", file=sys.stderr)
        return 2

    failures = 0
    for pv in pvs:
        policy = policy_for_pv(pv, args.policy)
        if args.dry_run:
            print(f"DRY-RUN register {pv} policy={policy}")
            continue

        try:
            if pv_already_registered(args.mgmt_url, pv, args.timeout):
                print(f"already-registered {pv}")
                continue
            ok, message = archive_pv(args.mgmt_url, pv, policy, args.timeout)
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            print(f"failed {pv}: {exc}", file=sys.stderr)
            failures += 1
            continue

        if ok:
            print(f"submitted {pv} policy={policy}: {message}")
        else:
            print(f"failed {pv}: {message}", file=sys.stderr)
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
