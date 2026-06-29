"""Shared utility functions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def merge_pvdb(groups: list[Any]) -> dict[str, Any]:
    """Merge PV databases and reject duplicate PV names."""
    merged: dict[str, Any] = {}
    for group in groups:
        for name, channel in group.pvdb.items():
            if name in merged:
                raise ValueError(f"Duplicate PV name: {name}")
            merged[name] = channel
    return merged
