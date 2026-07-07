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
EPICS_CA_ADDR_LIST="172.22.50.1 172.22.50.10"
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

`BDX_Physical_5s` is used for physical readbacks and applied setpoint readbacks,
including temperature, voltage, current, current limit, OVP, OCP, and chiller
setpoint values.

`BDX_State_Change` is used for boolean and state-transition PVs, including
communication OK/status, output state, run state, faults, warnings, alarms,
sensor status, and IOC state.

`BDX_Diagnostic_Change` is used for string and integer diagnostics, including
error code, error message, last-update timestamps, pump stage, cooling mode,
device status, and fault diagnosis.

Heartbeat counters are not archived. Command PVs, staged request PVs, apply
commands, clear-error commands, direct writable output/run controls, and
temporary GUI state are not archived.

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

`pv-lists/environment.txt` archives Raspberry MCP9808 physical temperature
values, sensor status/connectivity PVs, IOC state per sensor, error transitions,
and the last successful temperature-update timestamp. The current environment
IOC does not expose aggregate OK/failed sensor-count PVs; add them to this list
when the IOC contract provides them.

`pv-lists/psu.txt` archives LV1/LV2 physical readbacks, applied setpoint
readbacks, output state, OVP/OCP readbacks, communication state, IOC state,
all-outputs-off state, and error diagnostics.

`pv-lists/chiller.txt` archives controlled temperature, bath temperature,
applied setpoint, run/fault state, pump stage, cooling mode, Safe Mode readback
status, safe setpoint readback, communication-timeout readback, standby status,
deviation diagnostics, communication state, IOC state, and error diagnostics.
Pressure and external-temperature PVs remain excluded while they are disabled in
the default and main-server profiles.

`pv-lists/prototype.txt` is the duplicate-free union of the enabled subsystem
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

Edit `~/.config/bdx-archiver/archappl.env` and set:

```bash
BDX_ARCHIVER_EVALUATION_MODE=true
ARCHAPPL_PERSISTENCE_LAYER=
BDX_ARCHIVER_TOMCAT_TARBALL=/path/to/apache-tomcat-11.x.y.tar.gz
EPICS_CA_ADDR_LIST="172.22.50.1 172.22.50.10"
```

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

With the default configuration, `start.sh` also launches a background
auto-registration helper after the four Archiver Appliance components start.
For `--user-local`, the default PV lists resolve to:

```text
~/.local/share/bdx-archiver/app/pv-lists/psu.txt
~/.local/share/bdx-archiver/app/pv-lists/chiller.txt
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
EPICS_CA_ADDR_LIST="172.22.50.1 172.22.50.10"
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

Automatic registration is enabled by default:

```bash
BDX_ARCHIVER_AUTO_REGISTER=true
BDX_ARCHIVER_PV_LISTS=
BDX_ARCHIVER_REGISTER_RETRY_SECONDS=30
```

When `BDX_ARCHIVER_PV_LISTS` is empty, the startup scripts use
`$BDX_ARCHIVER_APP_DIR/pv-lists/psu.txt` and
`$BDX_ARCHIVER_APP_DIR/pv-lists/chiller.txt`, matching the current default
laboratory IOC profile. After `mgmt`, `engine`, `etl`, and `retrieval` respond
on component-specific BPL operations, the helper runs an equivalent of:

```bash
register-pvs.py \
  --mgmt-url "$BDX_ARCHIVER_MGMT_URL" \
  "$BDX_ARCHIVER_APP_DIR/pv-lists/psu.txt" \
  "$BDX_ARCHIVER_APP_DIR/pv-lists/chiller.txt"
```

Registration is idempotent; PVs reported as already registered are treated as
success. If the IOC is temporarily unavailable or a registration attempt fails,
the helper logs the failure and retries every
`BDX_ARCHIVER_REGISTER_RETRY_SECONDS` seconds. It writes a PID file under
`$BDX_ARCHIVER_STATE_DIR/run`, repeated `start.sh` calls do not create duplicate
helpers, and `stop.sh` stops the helper before stopping Tomcat.

Disable automatic registration explicitly when manual registration is desired:

```bash
BDX_ARCHIVER_AUTO_REGISTER=false
```

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

The registration script reports a per-PV outcome and returns non-zero if any PV
cannot be registered.

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
