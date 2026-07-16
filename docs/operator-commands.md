# Ubuntu Operator Commands

Slow control and the Archiver Appliance are independent lifecycle units. Normal
operators own the IOC and Phoebus lifecycle. Expert operators own the four
Archiver components and catalog repair.

Install or refresh the commands after repository changes:

```bash
cd ~/SlowControl/app/bdx-slow-control
bash scripts/install_user_commands.sh
```

The installer refreshes the editable virtual-environment installation, creates
links under `~/.local/bin`, and installs (but does not enable) the user Archiver
service definition.

## Canonical lifecycle commands

| Unit or action | Canonical command |
|---|---|
| Start slow control | `bdx_slow_control_start` |
| Stop slow control | `bdx_slow_control_kill` |
| Stop only the IOC | `bdx_slow_control_kill_ioc` |
| Stop only Phoebus | `bdx_slow_control_kill_phoebus` |
| Start the Archiver | `bdx_archiver_start` |
| Repair the Archiver catalog | `bdx_archiver_repair` |
| Audit the Archiver catalog | `bdx_archiver_audit` |
| Stop the Archiver | `bdx_archiver_kill` |

Use these names in new deployments, automation, and operator procedures.

### Normal slow control

Run the start command in an Ubuntu graphical desktop terminal:

```bash
bdx_slow_control_start
```

It checks the Raspberry environment IOC, starts the local project IOC when it
is not already listening, verifies the IOC listener and a live IOC PV, reports
Archiver endpoint health read-only, and launches Phoebus. It never starts,
stops, repairs, registers,
pauses, or resumes the Archiver. Archiver absence is not an error: Phoebus
starts for normal live Channel Access operation and historical data is simply
unavailable.

The command reports one of:

- `Archiver services: available and healthy.`
- `Archiver services: starting or temporarily unavailable.`
- `Archiver services: completely absent.`

The start command is idempotent: an IOC already listening on the configured
address and a Phoebus process matching its recorded PID are not duplicated.
The display defaults to `overview`; for example, `bdx_slow_control_start psu` opens
the PSU display. `BDX_MAIN_HOST` comes from the environment or the untracked
`config/runtime.env` and is `172.22.50.2` on the current prototype host.

Stop normal slow control with:

```bash
bdx_slow_control_kill
```

It stops Phoebus and project-owned local `bdx-prototype-ioc` processes. It does
not inspect or modify the Archiver and does not write an EPICS PV. Repeating the
command when processes are already stopped succeeds cleanly.

### Expert Archiver lifecycle

Start only management, engine, ETL, and retrieval with:

```bash
bdx_archiver_start
```

The command uses the expert environment in
`~/.config/bdx-archiver/archappl.env` and the installed user service. It
reconciles stale PID files, refuses duplicates and untracked occupied ports,
starts management, engine, ETL, and retrieval in that order, and bounds the
post-startup readiness wait. HTTP failures such as the expected temporary 500
responses are retried. Success requires a non-empty HTTP 2xx response from all
four supported version endpoints. A component exit during startup is reported.
The component JVMs are children of the user service manager, not the invoking
terminal or SSH session, so they survive shell exit, SSH disconnect, and
command-session completion. The service is installed but not enabled across a
host reboot.
The production user-local environment uses the bundled JDBM2 persistence file
under `~/.local/share/bdx-archiver/state/persistence`, so the catalog survives a
supported component restart independently of STS, MTS, and LTS sample data.

After all components are ready, the default command audits the complete
configured catalog and selectively repairs only missing PVs. Use this expert
option to start and validate components without catalog repair:

```bash
bdx_archiver_start --no-repair
```

The command never starts an IOC and never launches Phoebus. Low-level Tomcat
startup never performs bulk registration.

Stop only the Archiver with:

```bash
bdx_archiver_kill
```

The command stops retrieval, ETL, engine, and management in that order through
the supported Tomcat lifecycle. It reconciles stale PID files, treats an
already-stopped deployment as success, and reports remaining component
processes or occupied ports. It never stops an IOC or Phoebus and never removes
STS, MTS, LTS, or other archived data.

## Catalog audit and selective repair

Preferred expert commands are:

```bash
bdx_archiver_audit
bdx_archiver_repair
```

`bdx_archiver_audit` is equivalent to `bdx_archiver_repair --audit-only`.

Repair requires all four endpoints to be healthy. It first classifies all 18
essential configured PVs. A healthy PV with the expected effective policy, a
live connection and a valid prior event is skipped immediately: the default
does not pause/resume it or wait for a newly produced sample. All necessary
interventions are then started before a shared polling loop verifies workflow
completion, connection, first event and retrieval. `--timeout` is global to an
intervention wave instead of being multiplied by the number of PVs;
`--poll-interval` controls polling. `--verify-new-sample` opts into requiring an
event newer than repair start. Isolated failures receive one global retry wave.
`--stop-on-first-failure` retains diagnostic fail-fast behavior. Global
endpoint, catalog-API, retrieval-infrastructure, or unexpected external
workflow failures still stop immediately. There is no unconditional
full-catalog registration and no automatic engine restart. A complete final
catalog audit always runs when infrastructure remains healthy. The
single-appliance identity selects the upstream local-appliance capacity path;
it does not bypass metadata, queue, sampler, first-event, or retrieval checks.

Every repair writes a timestamped JSON report under the Archiver runtime
`state/run` directory. `--report-path FILE` selects an explicit location. Exit
status 0 means the complete catalog passed; 1 means the run completed with
missing or unhealthy PVs; 2 means a global infrastructure failure prevented
safe continuation.

Registrations outside the required 18-PV target are reported separately and do
not affect audit success. They are never re-registered by repair. Experts may
run `bdx_archiver_repair --pause-out-of-scope` to stop their future sampling.
This uses the supported pause operation, not delete: registrations, type
information, and existing STS/MTS/LTS history remain available for retrieval.

To require retrieval after a known restart time:

```bash
bdx_archiver_repair --retrieval-from 2026-07-14T09:30:00Z
```

The chiller target uses `BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV`, mapped by
the IOC from the device's `IN_PV_00` bath reading. The separate
`CONTROLLED_TEMPERATURE_RBV` (`IN_PV_01`) is not deleted; it remains a live and
potentially historical out-of-scope PV.

## Archiver status in the IOC and Phoebus

The main IOC polls the four local version endpoints every 10 seconds with a
one-second request timeout. Polling is asynchronous, bounded, read-only, and
does not make IOC startup depend on the Archiver. It exposes:

- `BDX:ARCHIVER:STATUS` (`AVAILABLE`, `STARTING`, `DEGRADED`, or `UNAVAILABLE`)
- `BDX:ARCHIVER:OK`
- `BDX:ARCHIVER:MGMT_OK`
- `BDX:ARCHIVER:ENGINE_OK`
- `BDX:ARCHIVER:ETL_OK`
- `BDX:ARCHIVER:RETRIEVAL_OK`
- `BDX:ARCHIVER:CATALOG_OK`
- `BDX:ARCHIVER:REQUIRED_TOTAL`
- `BDX:ARCHIVER:REQUIRED_HEALTHY`
- `BDX:ARCHIVER:CATALOG_STATUS`
- `BDX:ARCHIVER:LAST_CHECK`
- `BDX:ARCHIVER:ERROR_MESSAGE`

These are read-only software-status PVs; they cannot control the Archiver. The
general Phoebus overview shows textual component state and an alarm-sensitive
warning and the required-catalog count. `AVAILABLE` means all four services and
all 18 required PVs are healthy; `DEGRADED` means services are healthy but the
required catalog is incomplete; `UNAVAILABLE` means at least one service is
unavailable. Out-of-scope registrations do not affect this state. Live control
remains active in every state, and recovery clears the alarm without restarting
the IOC or Phoebus.

## Compatibility aliases

The following legacy aliases are maintained temporarily for compatibility with
existing installations and external procedures. They retain their existing
arguments and behavior, but must not be used in new deployments:

| Legacy alias | Canonical command |
|---|---|
| `start_slow_control` | `bdx_slow_control_start` |
| `kill_slow_control` | `bdx_slow_control_kill` |
| `start_archiver` | `bdx_archiver_start` |
| `kill_archiver` | `bdx_archiver_kill` |
| `bdx_slow_control_start_archiver` | `bdx_archiver_start` |
| `bdx_slow_control_repair_archiver` | `bdx_archiver_repair` |
| `bdx_slow_control_kill_archiver` | `bdx_archiver_kill` |

## Additional component command

`start-bdx-raspberry-ioc` starts or verifies the separately deployed Raspberry
environment IOC over SSH.

The Raspberry readiness PV remains `BDX:ENV:TEMP:T00:VALUE`. Component shutdown
commands validate process command lines before signalling them. `SIGKILL` is
used only when `--force` is supplied explicitly to IOC or Phoebus shutdown. The
chiller Archiver readiness/retrieval probe remains
`BDX:CHILLER:CHILLER1:RUN_STATE`.
