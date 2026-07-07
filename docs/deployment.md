# Deployment

## Local installation

```bash
git clone <repository-url>
cd bdx-slow-control
./scripts/bootstrap.sh
source .venv/bin/activate
```

## Configuration installation

A production-style layout is:

```text
/opt/bdx-slow-control/          source tree and virtual environment
/etc/bdx-slow-control/          environment file and installed profiles
/var/log/bdx-slow-control/      optional file logs
```

The default implementation logs to standard output. Under `systemd`, logs are stored in the journal.

## Service installation

Review all service files before installation. The installer copies one explicit profile
under `/etc/bdx-slow-control/profiles/<profile>`.

Install the main-server profile:

```bash
sudo ./scripts/install_systemd.sh main-server <runtime-user>
```

Install the full simulated prototype profile:

```bash
sudo ./scripts/install_systemd.sh prototype <runtime-user>
```

The installer does not enable or start services automatically. Start the main-server
service after reviewing `/etc/bdx-slow-control/bdx.env`:

```bash
sudo systemctl enable bdx-main-server-ioc
sudo systemctl start bdx-main-server-ioc
sudo systemctl status bdx-main-server-ioc
journalctl -u bdx-main-server-ioc -f
```

For the simulated prototype service, use `bdx-prototype-ioc` instead.

## Raspberry environment IOC deployment

Use the dedicated Raspberry installer when only the MCP9808 environment IOC should run
on a Raspberry Pi. It installs `bdx-environment-ioc`, not the full prototype IOC.

See `docs/raspberry.md`.

## EPICS Archiver Appliance deployment

BDX-owned Archiver Appliance deployment infrastructure is under:

```text
deploy/archiver-appliance/
```

That tree pins the official Archiver Appliance release, records the release
checksum, provides configuration templates, BDX PV lists, BDX policies, systemd
examples, health checks, registration and retrieval tools, and offline tests. It
does not contain downloaded Archiver Appliance artifacts, Tomcat runtimes, WAR or
JAR files, credentials, logs, databases, or archive data.

The final production host is not selected. The provisional persistent deployment
model targets a configurable Linux server, such as Ubuntu 22.04 with sudo access,
Java 21, Tomcat 11, persistent metadata storage, and dedicated archive storage.

Start with:

```bash
deploy/archiver-appliance/scripts/install.sh --check-only
```

Then follow `deploy/archiver-appliance/README.md` for the local evaluation,
provisional Ubuntu, PV registration, health-check, retrieval-test, backup,
upgrade, and uninstall procedures.

By default, the Archiver startup helper waits for the four Archiver Appliance
components to become healthy and then retries registration of the operational
PSU and chiller PV lists. Leave `BDX_ARCHIVER_PV_LISTS` empty to use those
repository defaults, or set it explicitly to a whitespace-separated list of
PV-list files.

## Channel Access interface

Set the interface in `/etc/bdx-slow-control/bdx.env`:

```bash
BDX_EPICS_INTERFACE=193.206.147.141
BDX_LOG_LEVEL=INFO
```

Do not bind production IOCs to unrelated VPN, container, or loopback interfaces.

## Profiles and PV uniqueness

Deployment profiles live under `config/profiles/`:

```text
config/profiles/default/       current lab operation: global, PSU, and chiller
config/profiles/prototype/     all simulated subsystems
config/profiles/main-server/   global, PSU, chiller, HV, DAQ; no environment IOC
config/profiles/raspberry/     environment MCP9808 IOC only
```

The main server and Raspberry Pi are separate Channel Access servers. They must never
publish the same PV names. The aggregated local IOC validates duplicate PV names inside
one configured profile; cross-host uniqueness is an operator responsibility.

## Validation

Before enabling automatic startup:

```bash
source /opt/bdx-slow-control/.venv/bin/activate
bdx-pv-list --config-dir /etc/bdx-slow-control/profiles/main-server
bdx-prototype-ioc --config-dir /etc/bdx-slow-control/profiles/main-server
```

From a client:

```bash
cainfo BDX:PSU:LV1:COMM_STATUS
cainfo BDX:PSU:LV1:COMM_OK
caget BDX:PSU:LV1:CH1:VOLTAGE_RBV
caput BDX:PSU:LV1:CH1:VOLTAGE_REQUEST 5.0
caput BDX:PSU:LV1:CH1:CURRENT_LIMIT_REQUEST 0.5
caput BDX:PSU:LV1:CH1:APPLY_CMD 1
```

The default laboratory chiller is CHILLER1 at `172.22.50.60:54321`. It uses the
LAUDA ECO Silver RE 1225 S driver with the same shared runtime monitoring period
as the PSU. The default period is 1.0 s, and changing
`BDX:GLOBAL:UPDATE_PERIOD_SET` changes both PSU and chiller polling in the
aggregated IOC. Blocking TCP communication is performed in a serialized worker
thread so a disconnected chiller does not block unrelated Channel Access
searches, gets, monitors, puts, or PSU polling. If a chiller poll takes longer
than the configured period, the IOC finishes that poll and then waits for the
next period; it does not queue overlapping chiller polls. IOC startup only reads
the current state; it does not send `START`, `STOP`, setpoint writes, Safe Mode
writes, or communication-timeout writes. The operator must explicitly start the
chiller through the confirmed `START` action.

Safe readback checks:

```bash
caget BDX:CHILLER:CHILLER1:COMM_OK
caget BDX:CHILLER:CHILLER1:COMM_STATUS
caget BDX:CHILLER:CHILLER1:CONTROLLED_TEMPERATURE_RBV
caget BDX:CHILLER:CHILLER1:BATH_TEMPERATURE_RBV
caget BDX:CHILLER:CHILLER1:SETPOINT_RBV
caget BDX:CHILLER:CHILLER1:RUN_STATE
```

If the chiller is disconnected, its communication and error PVs report the
failure and the IOC retries on the next shared runtime poll while other
subsystems remain responsive.

After deploying backend changes on the main server:

```bash
cd /opt/bdx-slow-control
git pull --ff-only
sudo ./scripts/install_systemd.sh main-server <runtime-user>
sudo systemctl restart bdx-main-server-ioc
sudo systemctl --no-pager --full status bdx-main-server-ioc
journalctl -u bdx-main-server-ioc -f
```

Regenerate only the default PSU and chiller operator displays when their
profiles or PV contracts change:

```bash
bdx-generate-displays \
  --config-dir config/profiles/default \
  --output-dir phoebus/displays \
  --only psu

bdx-generate-displays \
  --config-dir config/profiles/default \
  --output-dir phoebus/displays \
  --only chiller
```

## Hardware migration

For each subsystem:

1. implement a hardware driver with the same public methods as the simulator;
2. add driver construction in `drivers/factory.py`;
3. set `"mode": "hardware"` and provide transport parameters;
4. test communication-loss and reconnect behavior;
5. verify all setpoint/readback pairs;
6. verify safe state after IOC restart;
7. enable the corresponding service.

## PSU and chiller operator model

The PSU operator page uses `VOLTAGE_REQUEST`, `CURRENT_LIMIT_REQUEST`, and
`APPLY_CMD`. The IOC validates the voltage/current pair and configured power
envelope before sending hardware commands. Direct write PVs remain available
only in expert displays for compatibility and diagnostics.

The chiller operator page uses `SETPOINT_REQUEST` and `APPLY_SETPOINT_CMD`.
`START` and `STOP` are separate confirmed actions. `STOP` sends only the LAUDA
`STOP` command and places the unit in standby; Safe Mode configuration is a
separate expert-only setpoint/communication-timeout mechanism.

The default and main-server chiller profiles set `pressure_enabled=false` and
`external_temperature_enabled=false`. Disabled optional measurements are not
queried, plotted, archived, or shown as valid operator values.


## Split-host deployment

Example subsystem services are stored in `systemd/split-host/`. They are not installed by the deployment script. Use them only after assigning subsystems to separate hosts or defining non-conflicting Channel Access search ports and client address lists.

## Phoebus client deployment

Phoebus is not installed by the IOC deployment script. Configure the machine-specific launcher in:

```text
phoebus/phoebus.env
```

The launcher accepts either a direct `phoebus.sh` path, a Phoebus product directory, or a macOS application bundle. It generates the effective `settings.ini` at runtime and opens `overview.bob` by default.

For a local IOC and GUI:

```bash
BDX_CA_ADDR_LIST=127.0.0.1
BDX_CA_AUTO_ADDR_LIST=false
```

For a remote client, replace the address list with the IOC host address or the appropriate subnet broadcast address.

For the deployed main-server plus Raspberry layout:

```bash
BDX_CA_ADDR_LIST="<MAIN_SERVER_IP> <RASPBERRY_IP>"
BDX_CA_AUTO_ADDR_LIST=false
```

Only include both hosts when their configured profiles expose disjoint PV names.
