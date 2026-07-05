#!/usr/bin/env python3
"""Verify recent EPICS Archiver Appliance retrieval for one or more BDX PVs."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from archiver_common import DEFAULT_RETRIEVAL_URL, iso_utc, load_fixture, verify_retrieval


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--retrieval-url",
        default=os.environ.get("BDX_ARCHIVER_RETRIEVAL_DATA_URL", DEFAULT_RETRIEVAL_URL),
        help="Archiver Appliance retrieval base URL.",
    )
    parser.add_argument("--pv", action="append", required=True, help="PV to verify.")
    parser.add_argument("--minutes", type=float, default=10.0, help="Recent interval length.")
    parser.add_argument("--from-time", help="Explicit retrieval start time.")
    parser.add_argument("--to-time", help="Explicit retrieval end time.")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds.")
    parser.add_argument("--fixture", type=Path, help="Read a JSON fixture instead of calling HTTP.")
    parser.add_argument(
        "--allow-no-samples",
        action="store_true",
        help="Treat known PVs without recent samples as a successful endpoint check.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    now = datetime.now(timezone.utc)
    from_time = args.from_time or iso_utc(now - timedelta(minutes=args.minutes))
    to_time = args.to_time or iso_utc(now)

    failures = 0
    fixture_payload = load_fixture(args.fixture) if args.fixture else None

    for pv in args.pv:
        result = verify_retrieval(
            args.retrieval_url,
            pv,
            minutes=args.minutes,
            timeout=args.timeout,
            from_time=from_time,
            to_time=to_time,
            fixture_payload=fixture_payload,
        )
        if result.result == "successful retrieval":
            print(f"retrieval-ok {pv}")
        elif result.result == "known PV without samples" and args.allow_no_samples:
            print(f"known-no-samples {pv}")
        elif result.result == "known PV without samples":
            print(f"known-no-samples {pv}", file=sys.stderr)
            failures += 1
        elif result.result.startswith("endpoint failure"):
            print(f"endpoint-unavailable {pv}: {result.result}", file=sys.stderr)
            failures += 1
        else:
            print(f"unknown-pv {pv}", file=sys.stderr)
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
