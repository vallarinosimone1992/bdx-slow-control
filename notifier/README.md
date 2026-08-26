# BDX Notifier

Persistent EPICS Channel Access monitor for BDX slow-control alarms. Alarm
rules, timing, thresholds, and Telegram recipients are stored in `alarms.json`.

## Installation

```bash
cd /path/to/bdx-slow-control
./scripts/bootstrap.sh
mkdir -p "$HOME/.config/bdx-notifier"
chmod 700 "$HOME/.config/bdx-notifier"
cp notifier/config.env.example "$HOME/.config/bdx-notifier/config.env"
chmod 600 "$HOME/.config/bdx-notifier/config.env"
```

Edit `$HOME/.config/bdx-notifier/config.env` and insert
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. The real file must stay outside
the checkout and out of version control.

## Alarm policy

The default numeric policy is:

- `MINOR`: deviation at or above 5% for 5 seconds;
- `MAJOR`: deviation above 10% immediately;
- `MAJOR`: deviation at or above 5% for 20 seconds;
- `RESOLVED`: deviation below 5% for 5 seconds.

The monotonic clock is used for all durations. A rule can override every
percentage and duration independently:

```json
{
  "id": "sensor-specific-limit",
  "kind": "numeric",
  "label": "Sensor exceeds its upper limit",
  "pv": "BDX:ENV:TEMP:T01:VALUE",
  "mode": "above",
  "reference": {"value": 30.0},
  "overrides": {
    "minor_percent": 2.0,
    "minor_seconds": 10.0,
    "major_percent": 5.0,
    "major_seconds": 2.0,
    "major_sustained_percent": 2.0,
    "major_sustained_seconds": 30.0,
    "recovery_seconds": 10.0
  }
}
```

Numeric modes are:

- `deviation`: absolute percentage deviation from a reference value or PV;
- `above`: percentage above an upper limit;
- `below`: percentage below a lower limit.

State rules support `MAJOR` and `INTERLOCK`, independent activation and
recovery delays, and boolean alarm polarity. Interlock notifications and their
resolution are immediate by default unless configured otherwise.

State, stale, comparison, and range rules may instead define an ordered list of
stages. This supports transitions such as `MINOR` after 5 seconds, `MAJOR`
after 20 seconds, or `INTERLOCK` after 600 seconds:

```json
"stages": [
  {"level": "MAJOR", "after_seconds": 0},
  {"level": "INTERLOCK", "after_seconds": 600}
]
```

Additional rule kinds are:

- `stale`: alarms when a PV stops changing or stops publishing updates;
- `comparison`: compares a PV with another PV or a fixed value;
- `range`: checks fixed lower and upper limits;
- numeric `ratio`: compares a value with a limit or protection threshold.

Rules can have `conditions`, a `group` for persistent deduplication, and
`optional: true` for PVs that exist only in some deployment profiles. Repeated
channel rules are generated from `alarm_templates` in `alarms.json`.

The supplied configuration implements:

- global readiness, heartbeat, and interlock monitoring;
- the seven decoded LAUDA `STAT` bits, including low-level escalation from
  `MAJOR` to `INTERLOCK` after 600 seconds;
- chiller temperature deviation alarms;
- LV and simulated HV output consistency, setpoint deviation, OVP/OCP
  proximity, and a 30-second over-current interlock;
- 10--35 degC provisional ambient temperature limits;
- environmental, DAQ, and Archiver health/staleness monitoring.

An `INTERLOCK` level in the notifier is a high-priority notification. The
notifier remains read-only and does not itself issue `ALLOFF` or other hardware
commands.

## Telegram mentions

Telegram has no Bot API equivalent of `@all`, and a bot cannot enumerate every
ordinary group member. Define every mentionable person by numeric Telegram user
ID in `alarms.json`:

```json
"telegram": {
  "people": [
    {"user_id": 123456789, "name": "Operator One"},
    {"user_id": 987654321, "name": "Operator Two"}
  ],
  "major_people": [123456789],
  "interlock_people": "all",
  "verify_membership": true
}
```

For production, keep recipient identities outside Git by setting these entries
in `$HOME/.config/bdx-notifier/config.env`:

```text
TELEGRAM_PEOPLE=123456789:Operator One,987654321:Operator Two
TELEGRAM_MAJOR_PEOPLE=123456789
```

These values override the corresponding repository policy. `MINOR` does not
mention anyone. `MAJOR` mentions the IDs in `TELEGRAM_MAJOR_PEOPLE`.
`INTERLOCK` with `"all"` mentions all configured people. With membership
verification enabled, `getChatMember` excludes users no longer in the group;
the bot normally needs administrator access for reliable checks of other users.
Resolution messages use the same recipient policy as the originating level.

## Manual execution

For a local IOC:

```bash
export EPICS_CA_ADDR_LIST=127.0.0.1
export EPICS_CA_AUTO_ADDR_LIST=NO
.venv/bin/python notifier/notifier.py --dry-run
```

Remove `--dry-run` for Telegram delivery. `--notify-initial` reports alarms
that are already active when the complete EPICS baseline is acquired.

Test Telegram delivery without connecting to EPICS or reading/writing any PV:

```bash
.venv/bin/python notifier/notifier.py \
  --env-file "$HOME/.config/bdx-notifier/config.env" \
  --test-telegram
```

The command sends one harmless message, applies the configured MAJOR mention
policy, and exits. It is suitable for the real slow-control host before
starting the notifier service.

With the prototype IOC, representative injection commands are:

```bash
# LAUDA STAT: overtemperature, low level, then clear all bits
caproto-put BDX:CHILLER:CHILLER1:SIM_STAT_SET 0001000
caproto-put BDX:CHILLER:CHILLER1:SIM_STAT_SET 0000100
caproto-put BDX:CHILLER:CHILLER1:SIM_STAT_SET 0000000

# Communication loss and recovery
caproto-put BDX:PSU:LV1:SIM_COMM_FAILURE_SET 1
caproto-put BDX:PSU:LV1:SIM_COMM_FAILURE_SET 0

# Current and output-readback injection; NaN clears injected current
caproto-put BDX:PSU:LV1:CH1:SIM_CURRENT_SET 1.0
caproto-put BDX:PSU:LV1:CH1:SIM_CURRENT_SET NaN
caproto-put BDX:PSU:LV1:CH1:SIM_OUTPUT_MISMATCH_SET 1
caproto-put BDX:PSU:LV1:CH1:SIM_OUTPUT_MISMATCH_SET 0
```

## Slow-control lifecycle integration

`bdx_slow_control_start` starts this notifier in the background, records its
PID in `.runtime/bdx-stack/notifier.pid`, and writes output to
`.runtime/bdx-stack/notifier.log`. If the recorded notifier is already active,
it is left untouched. `bdx_slow_control_kill` stops it before stopping the IOC.

The notifier is shipped in the main `bdx-slow-control/notifier` directory and
uses the main project virtual environment. Older adjacent checkouts remain a
supported fallback:

```text
parent/
  bdx-slow-control/
  bdx-notifier/
```

The default secret location is outside the checkout. It may be made explicit
in the untracked slow-control `config/runtime.env`:

```bash
BDX_NOTIFIER_ENV_FILE="$HOME/.config/bdx-notifier/config.env"
BDX_NOTIFIER_DRY_RUN=false
```

`BDX_NOTIFIER_DIR` and `BDX_NOTIFIER_CONFIG` remain available for an external
installation or a different rule set. Relative overrides are resolved from
`BDX_NOTIFIER_DIR`.
