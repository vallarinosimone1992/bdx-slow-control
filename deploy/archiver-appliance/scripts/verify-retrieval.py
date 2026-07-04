#!/usr/bin/env python3
"""Verify recent EPICS Archiver Appliance retrieval for one or more BDX PVs."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_RETRIEVAL_URL = "http://127.0.0.1:17668/retrieval"


def retrieval_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/data/getData.json"):
        return base
    return base + "/data/getData.json"


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_json(url: str, timeout: float) -> tuple[int, Any, str]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(body), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed: Any = json.loads(body)
        except json.JSONDecodeError:
            parsed = None
        return exc.code, parsed, body


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


def block_has_samples(block: dict[str, Any]) -> bool:
    data = block.get("data", [])
    return isinstance(data, list) and len(data) > 0


def classify_payload(payload: Any, pv: str) -> str:
    text = json.dumps(payload).lower()
    if "unknown" in text or "not found" in text or "not currently being archived" in text:
        return "unknown"
    blocks = data_blocks_for_pv(payload, pv)
    if not blocks:
        return "unknown"
    if any(block_has_samples(block) for block in blocks):
        return "ok"
    return "no_samples"


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
        if fixture_payload is None:
            query = urllib.parse.urlencode({"pv": pv, "from": from_time, "to": to_time})
            url = retrieval_endpoint(args.retrieval_url) + "?" + query
            try:
                status, payload, body = fetch_json(url, args.timeout)
            except (OSError, urllib.error.URLError) as exc:
                print(f"endpoint-unavailable {pv}: {exc}", file=sys.stderr)
                failures += 1
                continue
            if status >= 500:
                print(f"endpoint-unavailable {pv}: HTTP {status}", file=sys.stderr)
                failures += 1
                continue
            if status == 404:
                print(f"unknown-pv {pv}", file=sys.stderr)
                failures += 1
                continue
            if payload is None:
                print(f"endpoint-unavailable {pv}: invalid JSON response: {body}", file=sys.stderr)
                failures += 1
                continue
        else:
            payload = fixture_payload

        result = classify_payload(payload, pv)
        if result == "ok":
            print(f"retrieval-ok {pv}")
        elif result == "no_samples" and args.allow_no_samples:
            print(f"known-no-samples {pv}")
        elif result == "no_samples":
            print(f"known-no-samples {pv}", file=sys.stderr)
            failures += 1
        else:
            print(f"unknown-pv {pv}", file=sys.stderr)
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
