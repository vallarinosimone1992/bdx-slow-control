#!/usr/bin/env python3
"""Register BDX PVs with the EPICS Archiver Appliance management BPL API."""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
from pathlib import Path

from archiver_common import (
    DEFAULT_MGMT_URL,
    PHYSICAL_POLICY,
    archive_pv,
    is_archivable_pv,
    policy_for_pv,
    pv_already_registered,
    read_pv_list,
)


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
        default=os.environ.get("BDX_ARCHIVER_DEFAULT_POLICY", PHYSICAL_POLICY),
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
        if not is_archivable_pv(pv):
            print(
                f"rejected {pv}: commands, staged requests, and heartbeat counters are not archived",
                file=sys.stderr,
            )
            failures += 1
            continue

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
