#!/usr/bin/env python3
"""BDX notifier runtime wrapper with EPICS control, heartbeat, and daily reports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import json
import logging
import os
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo

import notifier_core as core
from notifier_core import *  # noqa: F401,F403


LOG = logging.getLogger("bdx-notifier-control")
NOTIFIER_ENABLED_PV = "BDX:GLOBAL:NOTIFIER_ENABLED"
NOTIFIER_HEARTBEAT_PV = "BDX:GLOBAL:NOTIFIER_HEARTBEAT"
HEARTBEAT_SECONDS = 5.0
DAILY_REPORT_HOUR = 8
LOCAL_TIMEZONE = ZoneInfo("Europe/Rome")


class EventHistory:
    """Small persistent JSONL history used by the daily 24 h recap."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "events.jsonl"
        self.daily_state_path = self.state_dir / "daily_state.json"
        self._lock = threading.Lock()

    def append(self, event: core.AlarmEvent, *, delivered: bool) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "rule_id": event.rule_id,
            "label": event.label,
            "pv": event.pv,
            "level": event.level,
            "resolved": bool(event.resolved),
            "value": str(event.value),
            "delivered": bool(delivered),
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")

    def recent(self, hours: float = 24.0) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        if not self.path.exists():
            return []
        records: list[dict] = []
        with self._lock:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                    timestamp = datetime.fromisoformat(record["timestamp"])
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
                if timestamp >= cutoff:
                    records.append(record)
        return records

    def last_daily_date(self) -> str | None:
        try:
            payload = json.loads(self.daily_state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        value = payload.get("last_daily_date")
        return value if isinstance(value, str) else None

    def set_last_daily_date(self, value: str) -> None:
        payload = {"last_daily_date": value}
        temporary = self.daily_state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        temporary.replace(self.daily_state_path)


class ControlledBdxNotifier(core.BdxNotifier):
    """Add runtime controls without changing the validated alarm engine."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        state_dir = Path(
            os.getenv(
                "BDX_NOTIFIER_STATE_DIR",
                str(Path.home() / ".local" / "state" / "bdx-notifier"),
            )
        ).expanduser()
        self.history = EventHistory(state_dir)
        self.notifications_enabled = True
        self._heartbeat_pv = None
        self._control_subscriptions = []
        self._service_stop = threading.Event()
        self._heartbeat_counter = 0

    def _connect_once(self) -> None:
        super()._connect_once()
        assert self.context is not None
        enabled_pv, heartbeat_pv = self.context.get_pvs(
            NOTIFIER_ENABLED_PV,
            NOTIFIER_HEARTBEAT_PV,
            connection_state_callback=self._connection_callback,
        )
        for pv in (enabled_pv, heartbeat_pv):
            pv.wait_for_connection(timeout=self.connection_timeout)
        enabled_subscription = enabled_pv.subscribe()
        enabled_subscription.add_callback(self._value_callback)
        self._subscriptions.append(enabled_subscription)
        self._control_subscriptions.append(enabled_subscription)
        self._heartbeat_pv = heartbeat_pv
        try:
            response = enabled_pv.read(timeout=self.connection_timeout)
            self.notifications_enabled = core.value_as_bool(core.scalar_from_response(response))
        except Exception as exc:
            LOG.warning("Could not read initial notifier enable state: %s", exc)

    def _process_value(self, pv_name: str, value, now: float) -> None:
        if pv_name == NOTIFIER_ENABLED_PV:
            enabled = core.value_as_bool(value)
            if enabled != self.notifications_enabled:
                LOG.info("Notifier delivery state changed: %s", "ACTIVE" if enabled else "SNOOZED")
            self.notifications_enabled = enabled
            return
        super()._process_value(pv_name, value, now)

    def _deliver(self, event: core.AlarmEvent) -> None:
        for reduced_event in self.group_reducer.process(event):
            delivered = False
            if self.notifications_enabled:
                try:
                    self.sender.send_event(reduced_event)
                    delivered = True
                except core.NotificationDeliveryError as exc:
                    LOG.error(
                        "%s; event %s was not fully delivered",
                        exc,
                        reduced_event.rule_id,
                    )
            else:
                LOG.info(
                    "Notifier snoozed; suppressing %s level=%s resolved=%s",
                    reduced_event.rule_id,
                    reduced_event.level,
                    reduced_event.resolved,
                )
            self.history.append(reduced_event, delivered=delivered)

    def _daily_report_message(self) -> str:
        records = self.history.recent(24.0)
        activations = [item for item in records if not item.get("resolved", False)]
        resolutions = [item for item in records if item.get("resolved", False)]
        suppressed = [item for item in records if not item.get("delivered", True)]
        counts = {
            level: sum(1 for item in activations if item.get("level") == level)
            for level in ("MINOR", "MAJOR", "INTERLOCK")
        }
        lines = [
            "<b>✅ [BDX] DAILY NOTIFIER STATUS</b>",
            "Notifier is active.",
            "",
            "<b>Previous 24 h</b>",
            f"Alarm activations: {len(activations)}",
            f"Resolved transitions: {len(resolutions)}",
            (
                f"MINOR: {counts['MINOR']} | MAJOR: {counts['MAJOR']} | "
                f"INTERLOCK: {counts['INTERLOCK']}"
            ),
        ]
        if suppressed:
            lines.append(f"Events suppressed during snooze: {len(suppressed)}")
        if activations:
            lines.extend(["", "<b>Alarm activations</b>"])
            for item in activations[-10:]:
                try:
                    timestamp = datetime.fromisoformat(item["timestamp"]).astimezone(LOCAL_TIMEZONE)
                    stamp = timestamp.strftime("%d/%m %H:%M")
                except Exception:
                    stamp = "--"
                lines.append(
                    f"• {stamp} [{escape(str(item.get('level', '?')))}] "
                    f"{escape(str(item.get('label', item.get('rule_id', '?'))))}"
                )
            if len(activations) > 10:
                lines.append(f"… and {len(activations) - 10} more")
        else:
            lines.append("No alarm activations recorded.")
        return "\n".join(lines)

    def _maybe_daily_report(self) -> None:
        local_now = datetime.now(LOCAL_TIMEZONE)
        today = local_now.date().isoformat()
        if self.history.last_daily_date() == today:
            return
        if local_now.hour < DAILY_REPORT_HOUR:
            return
        if local_now.hour > DAILY_REPORT_HOUR:
            self.history.set_last_daily_date(today)
            return

        self.history.set_last_daily_date(today)
        if not self.notifications_enabled:
            LOG.info("Skipping 08:00 daily notifier report because notifications are snoozed")
            return
        try:
            self.sender.telegram._send(self._daily_report_message())
        except core.NotificationDeliveryError as exc:
            LOG.error("Daily notifier report delivery failed: %s", exc)

    def _service_loop(self) -> None:
        next_heartbeat = 0.0
        while not self._service_stop.wait(1.0):
            now = time.monotonic()
            heartbeat_pv = self._heartbeat_pv
            if heartbeat_pv is not None and now >= next_heartbeat:
                self._heartbeat_counter = (self._heartbeat_counter + 1) % 2_147_483_647
                try:
                    heartbeat_pv.write(self._heartbeat_counter, timeout=2.0)
                except Exception as exc:
                    LOG.warning("Could not update notifier EPICS heartbeat: %s", exc)
                next_heartbeat = now + HEARTBEAT_SECONDS
            if heartbeat_pv is not None:
                self._maybe_daily_report()

    def run(self) -> None:
        service_thread = threading.Thread(
            target=self._service_loop,
            name="bdx-notifier-service",
            daemon=True,
        )
        service_thread.start()
        try:
            super().run()
        finally:
            self._service_stop.set()
            service_thread.join(timeout=3.0)


core.BdxNotifier = ControlledBdxNotifier


def main(argv: list[str] | None = None) -> int:
    return core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
