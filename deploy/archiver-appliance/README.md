# BDX EPICS Archiver Appliance Deployment

This directory contains BDX-owned deployment infrastructure for the EPICS
Archiver Appliance. It intentionally does not contain third-party source trees,
WAR files, JAR files, Tomcat runtimes, downloaded archives, logs, archive data,
databases, credentials, or secrets.

## Selected Release

The pinned stable release is:

```text
Archiver Appliance: 2.3.1
Artifact: archappl_v2.3.1.tar.gz
URL: https://github.com/archiver-appliance/epicsarchiverap/releases/download/2.3.1/archappl_v2.3.1.tar.gz
SHA-256: ce2eabe57915a99bc9be22d29d400f112f63931b5d5af9394e8504702d16722f
Java: JDK 21 or later
Servlet container: Apache Tomcat 11
```

Release `2.4.0` exists upstream as a prerelease and is not selected here.

The upstream license is the EPICS Archiver Appliance software license from the
official release. Keep the upstream `LICENSE`, `NOTICE`, and bundled license
files with any downloaded runtime artifact.

## Architecture

The Archiver Appliance is deployed as four official WAR components:

```text
mgmt       management UI and BPL API
engine     Channel Access sampling engine
etl        data movement between stores
retrieval  HTTP data retrieval API
```

The BDX scripts generate:

```text
appliances.xml  appliance topology and component URLs
policies.py     official policy interface with BDX policy names
pv-lists/*.txt  BDX PV registration lists
```

The persistent deployment is the primary model. A user-local layout is also
supported for evaluation.

## Storage Layout

Default persistent paths:

```text
/opt/bdx-archiver          application scripts and staged release metadata
/etc/bdx-archiver          site configuration
/var/lib/bdx-archiver      state, Tomcat bases, STS/MTS/LTS stores
/var/log/bdx-archiver      component logs
/var/cache/bdx-archiver    downloaded release archive cache
```

Runtime archive data must never be stored in the Git checkout.

The data stores are separated as:

```text
tmp          temporary files
sts          short-term store
mts          medium-term store
lts          long-term store
persistence  local persistence metadata if used
```

BDX archiving is continuous, 24 hours per day. It is not gated by run state:
archive sampling continues before, during, and after data-taking runs.

## Prerequisites

The scripts report missing prerequisites but never install OS packages.

Ubuntu 22.04 hints:

```bash
sudo apt install openjdk-21-jdk curl tar
```

Install Apache Tomcat 11 from the official Tomcat distribution if it is not
available from the OS repositories.

Rocky Linux 9 hints:

```bash
sudo dnf install java-21-openjdk java-21-openjdk-devel curl tar
```

Install Apache Tomcat 11 from the official Tomcat distribution if it is not
available from the OS repositories.

The management service should remain on a trusted network. These scripts do not
modify host firewall rules.

## Channel Access

Configure Channel Access in the private environment file:

```bash
EPICS_CA_ADDR_LIST="172.22.50.2 172.22.50.10"
EPICS_CA_AUTO_ADDR_LIST=NO
EPICS_CA_SERVER_PORT=5064
EPICS_CA_REPEATER_PORT=5065
```

The example includes a future main IOC server and the Raspberry environment IOC.
Do not hard-code prototype addresses in scripts or PV lists.

## Archive Policies

BDX uses deterministic policy assignment. The registration script passes an
explicit policy for every PV, and `config/policies.py` contains the same
PV-name classification for deployments that use server-side policy selection.

Policy names:

```text
BDX_Physical_5s       MONITOR, nominal 5 s
BDX_State_Change      MONITOR, nominal 1 s
BDX_Diagnostic_Change MONITOR, nominal 5 s
```

`BDX_Physical_5s` is used by the essential catalog for physical temperature,
voltage and current readbacks and applied voltage/chiller setpoint readbacks.
Its effective method is `MONITOR` with a nominal 5-second period and zero
Archiver-side value delta/deadband. This is not a forced 5-second scan: the
engine subscribes to EPICS archive monitor events and stores events delivered by
the IOC. It does not synthesize periodic copies of an unchanged value merely to
fill five-second intervals; whether an IOC posts an event for an unchanged value
remains part of that record's EPICS behavior.

`BDX_State_Change` is used for boolean and state-transition PVs, including
communication OK/status, output state, run state, faults, warnings, alarms,
sensor status, and IOC state.

`BDX_Diagnostic_Change` is used for string and integer diagnostics, including
error code, error message, last-update timestamps, pump stage, cooling mode,
device status, and fault diagnosis.

Heartbeat counters and `LAST_UPDATE` diagnostics are not archived. Every stored
Archiver event already retains the EPICS event timestamp associated with that
sample, so value history does not require a separately archived timestamp PV.
`LAST_UPDATE` remains a live diagnostic heartbeat. Command PVs, staged request
PVs, apply commands, clear-error commands, direct writable output/run controls,
and temporary GUI state are also excluded.

## Retention

Short-term storage keeps the existing hourly partition configuration.

Medium-term storage uses daily partitions and keeps 60 days by default:

```bash
BDX_ARCHIVER_MEDIUM_TERM_HOLD_DAYS=60
```

The generated policy uses this value in the MTS storage plugin URL.

Long-term storage uses yearly partitions and has no automatic deletion or hold
limit. No expired LTS data is routed to a black-hole store. LTS growth must be
monitored operationally through filesystem and appliance health checks.

The previous `BDX_ARCHIVER_LONG_TERM_HOLD_YEARS` setting is intentionally not
part of the repository configuration because it would imply an LTS deletion
policy that BDX has not approved.

## Archived PV Lists

The initial operational archive is intentionally limited to 18 essential
physical measurements and applied setpoints. `pv-lists/environment.txt`
contains only T00 through T03 temperature values. `pv-lists/psu.txt` contains,
for both channels of LV1 and LV2, the configured voltage readback, measured
voltage, and measured current. `pv-lists/chiller.txt` contains only the applied
setpoint and real bath-temperature readback (`IN_PV_00`). The distinct
controlled-temperature readback (`IN_PV_01`) remains available live and any
existing history remains retrievable, but it is outside the required catalog.
Diagnostics, state PVs and heartbeat timestamps remain
available live but are not part of the required archive catalog.

`pv-lists/prototype.txt` is the duplicate-free 18-PV union of the subsystem
lists. Validate the PV lists with:

```bash
python -m pytest -q tests/test_archiver_deployment.py
```

## Local Evaluation

Create a user-local environment file:

```bash
mkdir -p ~/.config/bdx-archiver
cp deploy/archiver-appliance/config/archappl.env.example \
  ~/.config/bdx-archiver/archappl.env
```

Edit `~/.config/bdx-archiver/archappl.env` and set the bundled durable
single-appliance persistence layer:

```bash
BDX_ARCHIVER_EVALUATION_MODE=false
ARCHAPPL_PERSISTENCE_LAYER=org.epics.archiverappliance.config.persistence.JDBM2Persistence
ARCHAPPL_PERSISTENCE_LAYER_JDBM2FILENAME="$HOME/.local/share/bdx-archiver/state/persistence/archapplconfig"
BDX_ARCHIVER_TOMCAT_TARBALL=/path/to/apache-tomcat-11.x.y.tar.gz
EPICS_CA_ADDR_LIST="172.22.50.2 172.22.50.10"
```

JDBM2 persists catalog type information and pending requests across supported
component restarts. Its file belongs under the user-local persistence directory;
it is separate from STS, MTS, and LTS sample storage. Use the in-memory layer
only for deliberately disposable evaluation because it returns with an empty
catalog after every restart.

Then stage and configure:

```bash
deploy/archiver-appliance/scripts/install.sh \
  --env ~/.config/bdx-archiver/archappl.env \
  --user-local \
  --download

deploy/archiver-appliance/scripts/configure.sh \
  --env ~/.config/bdx-archiver/archappl.env \
  --user-local
```

Start and stop manually:

```bash
~/.local/share/bdx-archiver/app/scripts/start.sh \
  --env ~/.config/bdx-archiver/archappl.env \
  --user-local

~/.local/share/bdx-archiver/app/scripts/stop.sh \
  --env ~/.config/bdx-archiver/archappl.env \
  --user-local
```

These low-level scripts affect only the four Tomcat components and never launch
catalog registration. For the persistent user-local lifecycle, install the
operator commands and use `bdx_archiver_start` and `bdx_archiver_kill`. The
foreground Tomcats then belong to `bdx-archiver-user.service` and the user
service manager rather than the invoking shell, so they survive shell exit,
SSH disconnect, and command-session completion. The installer deliberately
does not enable automatic restart across a reboot.

For `--user-local`, the default expert repair PV lists resolve to:

```text
~/.local/share/bdx-archiver/app/pv-lists/psu.txt
~/.local/share/bdx-archiver/app/pv-lists/chiller.txt
~/.local/share/bdx-archiver/app/pv-lists/environment.txt
```

## Provisional Ubuntu 22.04 Deployment

The final production host is not selected. A provisional persistent deployment
on an Ubuntu 22.04 server with sudo access is feasible if Java 21, Tomcat 11,
persistent metadata storage, and adequate filesystem storage are provided.

```bash
sudo install -d -m 0755 /etc/bdx-archiver
sudo cp deploy/archiver-appliance/config/archappl.env.example \
  /etc/bdx-archiver/archappl.env
sudo editor /etc/bdx-archiver/archappl.env
```

Set at least:

```bash
BDX_ARCHIVER_TOMCAT_HOME=/opt/bdx-archiver/tomcat
BDX_ARCHIVER_TOMCAT_TARBALL=/path/to/apache-tomcat-11.x.y.tar.gz
ARCHAPPL_PERSISTENCE_LAYER=<site-selected persistent layer>
EPICS_CA_ADDR_LIST="172.22.50.2 172.22.50.10"
```

Stage the BDX deployment files and optionally download the pinned release:

```bash
sudo deploy/archiver-appliance/scripts/install.sh \
  --env /etc/bdx-archiver/archappl.env \
  --download

sudo /opt/bdx-archiver/scripts/configure.sh \
  --env /etc/bdx-archiver/archappl.env
```

Install and start the system service after reviewing the generated configuration:

```bash
sudo install -m 0644 /opt/bdx-archiver/systemd/bdx-archiver.service \
  /etc/systemd/system/bdx-archiver.service
sudo systemctl daemon-reload
sudo systemctl enable bdx-archiver
sudo systemctl start bdx-archiver
sudo systemctl --no-pager --full status bdx-archiver
```

## PV Registration

Automatic registration is disabled during low-level component startup:

```bash
BDX_ARCHIVER_AUTO_REGISTER=false
BDX_ARCHIVER_PV_LISTS=
BDX_ARCHIVER_REGISTER_RETRY_SECONDS=30
```

When `BDX_ARCHIVER_PV_LISTS` is empty, the expert repair command uses
`$BDX_ARCHIVER_APP_DIR/pv-lists/psu.txt` and
`$BDX_ARCHIVER_APP_DIR/pv-lists/chiller.txt`, and
`$BDX_ARCHIVER_APP_DIR/pv-lists/environment.txt`, matching the complete deployed
prototype split across the main host and Raspberry. The low-level component
start script never invokes registration. After all four supported readiness
endpoints are healthy, `bdx_archiver_start` invokes staged selective repair
unless `--no-repair` is supplied.

The legacy bulk command remains available for explicit compatibility use and
runs an equivalent of:

```bash
register-pvs.py \
  --mgmt-url "$BDX_ARCHIVER_MGMT_URL" \
  "$BDX_ARCHIVER_APP_DIR/pv-lists/psu.txt" \
  "$BDX_ARCHIVER_APP_DIR/pv-lists/chiller.txt" \
  "$BDX_ARCHIVER_APP_DIR/pv-lists/environment.txt"
```

Do not enable the legacy automatic helper for normal operation.

Dry-run registration:

```bash
/opt/bdx-archiver/scripts/register-pvs.py \
  --dry-run \
  /opt/bdx-archiver/pv-lists/environment.txt \
  /opt/bdx-archiver/pv-lists/psu.txt \
  /opt/bdx-archiver/pv-lists/chiller.txt
```

Register PVs using the management BPL API:

```bash
/opt/bdx-archiver/scripts/register-pvs.py \
  --mgmt-url http://127.0.0.1:17665/mgmt/bpl \
  /opt/bdx-archiver/pv-lists/environment.txt \
  /opt/bdx-archiver/pv-lists/psu.txt \
  /opt/bdx-archiver/pv-lists/chiller.txt
```

The legacy registration script remains available for compatibility. Normal
startup and recovery use the staged selective repair command:

```bash
/opt/bdx-archiver/scripts/repair-archiver.sh \
  --env /etc/bdx-archiver/archappl.env
```

This requires all four components to be healthy and first audits the complete
18-PV required catalog. Healthy PVs take a fast path: valid connection, last
event and effective policy are sufficient, so they are neither paused nor
forced to produce a new sample. Interventions for all missing, paused,
disconnected, pending or incorrectly configured required PVs are initiated
before a single global polling phase checks workflow completion, connection,
first event and retrieval. Thus the timeout is per wave, not per PV. Use
`--verify-new-sample` when a new event after repair start is specifically
required, and `--timeout`/`--poll-interval` to tune the bounded global wait.
Timed-out repair-owned workflows are aborted without deleting registration or
history, and isolated failures receive one retry wave. `--stop-on-first-failure`
keeps the diagnostic fail-fast mode. Endpoint loss, an unexpected external
workflow, or an unavailable catalog/retrieval API remains a global stop. There
is no full-catalog fallback or automatic component restart. The command writes
a timestamped JSON report under `$BDX_ARCHIVER_STATE_DIR/run`, reports exact
final failures, returns 1 for completed partial success, and returns 2 for a
global infrastructure failure.

Audit output reports registrations outside the required set separately. They do
not affect required-catalog success and repair never re-registers them. The
expert `--pause-out-of-scope` option stops future sampling of those
registrations using the supported pause BPL. Pause leaves each PV registered and
preserves its type information and STS/MTS/LTS history for retrieval. Repair
never invokes a delete/purge BPL and never removes archive storage.

In a configured single-appliance deployment it includes
`BDX_ARCHIVER_APPLIANCE_ID` in each request, selecting the upstream
skip-capacity-planning path while retaining the named BDX policy and every
queue, first-event, and retrieval validation gate.

## Health Checks

```bash
/opt/bdx-archiver/scripts/healthcheck.sh \
  --env /etc/bdx-archiver/archappl.env
```

With retrieval of a known BDX PV:

```bash
/opt/bdx-archiver/scripts/healthcheck.sh \
  --env /etc/bdx-archiver/archappl.env \
  --check-pv
```

## Retrieval Tests

```bash
/opt/bdx-archiver/scripts/verify-retrieval.py \
  --retrieval-url http://127.0.0.1:17668/retrieval \
  --pv BDX:ENV:TEMP:T00:VALUE
```

The script distinguishes endpoint failure, unknown PV, known PV without recent
samples, and successful retrieval.

## Batch Archive Validation

`scripts/test-archive-batches.py` is a non-destructive validation tool for
isolating Archiver registration, retrieval, and Channel Access protocol
problems one small PV batch at a time. It never stops or restarts the IOC or
Archiver, never truncates IOC logs, never changes IOC configuration, and never
edits repository PV-list files.

The current Python 3.13 prototype test setup uses:

```text
.venv313
config/profiles/prototype
/tmp/bdx-ioc-python313.log
http://127.0.0.1:17665/mgmt/bpl
http://127.0.0.1:17668/retrieval
deploy/archiver-appliance/pv-lists/prototype.txt
```

The tool first runs:

```bash
bdx-pv-list --config-dir <config-dir>
```

It then tests only the intersection between the requested PV-list files and the
PVs exposed by the selected IOC profile. Requested-but-absent PVs are written to
`missing-pvs.txt` and reported, but they are not removed from production lists.
For example, the prototype profile currently defines only one simulated
environment temperature sensor. The Raspberry production archive list keeps
`T00`, `T02`, and `T03` because the deployed Raspberry IOC exposes those PVs.
Their absence from the prototype profile does not mean the production archive
lists are wrong.

Dry-run, no registration:

```bash
.venv313/bin/python deploy/archiver-appliance/scripts/test-archive-batches.py \
  --config-dir config/profiles/prototype \
  --pv-list deploy/archiver-appliance/pv-lists/prototype.txt \
  --wait-seconds 0
```

Chiller-only validation:

```bash
.venv313/bin/python deploy/archiver-appliance/scripts/test-archive-batches.py \
  --config-dir config/profiles/prototype \
  --pv-list deploy/archiver-appliance/pv-lists/prototype.txt \
  --subsystem chiller \
  --batch-size 5 \
  --wait-seconds 75
```

PSU-only validation:

```bash
.venv313/bin/python deploy/archiver-appliance/scripts/test-archive-batches.py \
  --config-dir config/profiles/prototype \
  --pv-list deploy/archiver-appliance/pv-lists/prototype.txt \
  --subsystem psu \
  --batch-size 5 \
  --wait-seconds 75
```

Environment-only validation:

```bash
.venv313/bin/python deploy/archiver-appliance/scripts/test-archive-batches.py \
  --config-dir config/profiles/prototype \
  --pv-list deploy/archiver-appliance/pv-lists/prototype.txt \
  --subsystem environment \
  --batch-size 5 \
  --wait-seconds 75
```

Complete prototype validation with actual registration of unknown PVs:

```bash
.venv313/bin/python deploy/archiver-appliance/scripts/test-archive-batches.py \
  --config-dir config/profiles/prototype \
  --pv-list deploy/archiver-appliance/pv-lists/prototype.txt \
  --batch-size 5 \
  --wait-seconds 75 \
  --register
```

The batch order is deterministic: chiller physical, chiller state, chiller
diagnostic, PSU physical, PSU state, PSU diagnostic, environment physical,
environment state, environment diagnostic. This helps isolate which subsystem
or policy category causes additional caproto protocol errors.

Each output directory contains:

```text
ioc-pvs.txt                 all PVs generated from the selected IOC profile
requested-pvs.txt           requested Archiver PVs after comment/duplicate removal
present-pvs.txt             requested PVs present in the selected IOC profile
missing-pvs.txt             requested PVs absent from the selected IOC profile
batch-*.txt                 PVs tested in each batch
batch-*-registration.log    dry-run or registration outcome per PV
batch-*-caproto-errors.log  new protocol-error lines after the batch started
summary.json                machine-readable result summary
summary.csv                 tabular result summary
final-report.txt            concise human-readable report
```

The protocol-error counter inspects only IOC log content appended after each
batch begins. It counts new occurrences of `Unrecognized subscriptionid`,
`Unknown Channel sid`, and `RemoteProtocolError`. By default, any new occurrence
fails the batch. Use `--continue-on-protocol-errors` only when you want the tool
to report those occurrences without stopping or failing solely because of them.

## Phoebus Integration

The Phoebus launcher combines live Channel Access samples with Archiver
Appliance history by default, using `http://127.0.0.1:17668/retrieval` unless
overridden. Use the same retrieval endpoint configured for the appliance when
launching from another host:

```bash
BDX_ARCHIVER_ENABLED=true \
BDX_ARCHIVER_URL=http://<ARCHIVER_HOST>:17668/retrieval \
BDX_ARCHIVER_NAME="BDX Archiver" \
./scripts/launch_phoebus.sh overview
```

The launcher writes Phoebus Data Browser preferences with a `pbraw://` archive
URL. If the retrieval service is temporarily unavailable, Data Browser keeps the
live Channel Access traces unless `BDX_ARCHIVER_STRICT_CHECK=true` and the
optional preflight check fails.

Disable archive integration explicitly for live-only Phoebus operation:

```bash
BDX_ARCHIVER_ENABLED=false ./scripts/launch_phoebus.sh overview
```

## Backup And Restore

Back up configuration and metadata:

```bash
/opt/bdx-archiver/scripts/backup-config.sh \
  --env /etc/bdx-archiver/archappl.env
```

This does not create a consistent live backup of archive data under STS/MTS/LTS.
Use a storage-specific snapshot or backup method for archive data.

Restore procedure:

1. Install the same pinned Archiver Appliance release and BDX deployment scripts.
2. Restore `/etc/bdx-archiver`, topology, policies, PV lists, and persistence metadata.
3. Restore archive data using the storage-specific consistent backup.
4. Run `configure.sh`.
5. Start the service and run `healthcheck.sh`.

## Upgrade Procedure

1. Select an official stable release.
2. Update `VERSION`, `CHECKSUMS`, and `archappl.env.example`.
3. Validate Java and Tomcat compatibility.
4. Download the artifact outside the repository.
5. Verify SHA-256.
6. Stop the service.
7. Back up configuration and persistence metadata.
8. Deploy the new WAR files.
9. Run health and retrieval checks.

Do not commit downloaded release artifacts.

## Uninstall

Stop and remove staged application files while preserving state:

```bash
sudo /opt/bdx-archiver/scripts/uninstall.sh \
  --env /etc/bdx-archiver/archappl.env \
  --yes
```

Remove state and configuration only after a confirmed backup:

```bash
sudo /opt/bdx-archiver/scripts/uninstall.sh \
  --env /etc/bdx-archiver/archappl.env \
  --yes \
  --purge-state
```

## Future JLab Integration Boundary

No JLab central archiving or database integration is assumed here. This
repository owns BDX PV lists, policies, deployment scripts, checksums,
configuration templates, systemd examples, and health checks. Future JLab
integration should replace or wrap these templates at the deployment boundary
without committing site credentials, central database details, or private
network assumptions to this repository.
