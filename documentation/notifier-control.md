# Notifier runtime control

The BDX notifier can be temporarily snoozed from the Phoebus overview without stopping the process or disabling alarm evaluation.

## EPICS PVs

| PV | Purpose |
| --- | --- |
| `BDX:GLOBAL:NOTIFIER_ENABLED` | Effective notification-delivery state. `On` means Telegram/email delivery is enabled. |
| `BDX:GLOBAL:NOTIFIER_ONLINE` | Process heartbeat status. It becomes `Off` when no notifier heartbeat is received for 15 s. |
| `BDX:GLOBAL:NOTIFIER_STATUS` | Human-readable state: `ACTIVE`, `SNOOZED`, or `OFFLINE`. |
| `BDX:GLOBAL:NOTIFIER_HEARTBEAT` | Counter written by the notifier every 5 s. |
| `BDX:GLOBAL:NOTIFIER_SNOOZE_MINUTES` | Requested snooze duration in minutes. Must be positive. |
| `BDX:GLOBAL:NOTIFIER_SNOOZE_CMD` | Command to start the requested snooze interval. |
| `BDX:GLOBAL:NOTIFIER_RESUME_CMD` | Command to immediately re-enable notification delivery. |
| `BDX:GLOBAL:NOTIFIER_SNOOZE_UNTIL` | Local Europe/Rome date/time at which delivery will be re-enabled automatically. |
| `BDX:GLOBAL:NOTIFIER_LAST_HEARTBEAT` | Timestamp of the last notifier heartbeat received by the IOC. |

## Snooze semantics

Snoozing suppresses Telegram and email delivery only. The notifier process remains running, continues evaluating all alarm rules, and records alarm transitions in its persistent history. When the snooze expires, or `RESUME` is pressed, normal delivery resumes for subsequent alarm transitions.

The history is stored by default in:

```text
~/.local/state/bdx-notifier/events.jsonl
```

The directory can be overridden with `BDX_NOTIFIER_STATE_DIR`.

## Daily status report

At 08:00 Europe/Rome, if notification delivery is active, the notifier sends one Telegram status message. The message confirms that the notifier is active and summarizes the previous 24 h:

- total alarm activations;
- total resolved transitions;
- MINOR / MAJOR / INTERLOCK activation counts;
- number of events suppressed during snooze;
- up to the 10 most recent alarm activations.

If the notifier is snoozed during the 08:00 hour, that day's report is skipped. The last report date is persisted in `~/.local/state/bdx-notifier/daily_state.json` to avoid duplicate daily messages after process restarts.
