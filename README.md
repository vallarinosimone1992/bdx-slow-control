# BDX Slow Control

Modular EPICS slow-control framework for the BDX prototype and future detector deployment.

This repository replaces the earlier monolithic `test_ioc.py` prototype. Backward compatibility is intentionally not provided. The code is organized around independent subsystem IOC groups and hardware-independent drivers, while the single-host prototype runs the groups in one caproto Channel Access server process.

All software-facing material in this repository is written in English.

## Implemented prototype subsystems

- low-voltage power supply;
- chiller;
- environmental temperature, humidity, and pressure sensors;
- high-voltage power supply;
- DAQ crate configuration and state interface;
- global state, update timing, all-off, and simulated interlock handling.

The initial hardware backends are simulations. Hardware implementations can be added without changing the IOC-facing interfaces or the Phoebus PV contract.

## Operator control model

Low-voltage PSU and chiller operator pages use staged request PVs plus explicit
apply commands. Editing an operator text field does not immediately write
hardware.

For CPX400DP low-voltage PSU channels:

```text
BDX:PSU:LV1:CH1:VOLTAGE_REQUEST
BDX:PSU:LV1:CH1:CURRENT_LIMIT_REQUEST
BDX:PSU:LV1:CH1:APPLY_CMD
```

The IOC validates voltage and current together before writing either value.
Default CPX400DP software limits are `0-60 V`, `0-20 A`, and `420 W` maximum
requested voltage-current product. These defaults represent instrument
capability limits and can be overridden in the JSON profile through
`software_limits` and per-channel `channel_limits`.

The direct compatibility PVs, such as `VOLTAGE_SET`, `CURRENT_LIMIT_SET`,
`OVP_SET`, and `OCP_SET`, remain available in expert displays. Hardware
readbacks use device queries and are not derived from writable command PVs.

For the LAUDA chiller:

```text
BDX:CHILLER:CHILLER1:SETPOINT_REQUEST
BDX:CHILLER:CHILLER1:APPLY_SETPOINT_CMD
```

The IOC enforces the configured setpoint range, currently `5-40 degC`, and
exposes setpoint-deviation diagnostics. Pressure and external temperature are
disabled in the main-server profile because those measurements are not
available; disabled optional measurements are not queried or shown on the
operator page.

## Repository layout

```text
bdx-slow-control/
├── config/
│   ├── deployment/         Host deployment configuration
│   ├── profiles/           Installable IOC configuration profiles
│   └── examples/           Non-installed configuration examples
├── deploy/                 External deployment infrastructure
├── docs/                   Architecture and deployment notes
├── phoebus/
│   ├── displays/           Generated .bob displays and PV table
│   ├── settings/           Phoebus settings template
│   └── phoebus.env.example
├── scripts/                Bootstrap, IOC, and Phoebus launchers
├── src/bdx_slow_control/   Python package
│   ├── drivers/            Driver interfaces and backends
│   └── iocs/               IOC groups
├── systemd/                Service examples
└── tests/                  Unit and display-contract tests
```

## Requirements

- Python 3.10 or newer;
- `caproto` for the IOC and Channel Access clients;
- EPICS Base client tools are optional;
- a separate Phoebus installation for the graphical interface.

## Bootstrap

```bash
cd bdx-slow-control
./scripts/bootstrap.sh
source .venv/bin/activate
```

The bootstrap script installs the package in editable mode and regenerates the Phoebus displays from the configured PV database.

## Run the default laboratory IOC

The default operational profile is `config/profiles/default`. It contains only
the subsystems currently ready for routine laboratory operation:

- `global`;
- `psu`.

The PSU profile instantiates LV1 at `172.22.50.20:9221` and LV2 at
`172.22.50.21:9221`, using the CPX400DP hardware driver, channels 1 and 2, and
a 1.0 s polling period. The default profile does not include the chiller yet.
Startup does not enable PSU outputs or write operator setpoints; outputs change
only after explicit operator commands.

Run the default aggregated IOC:

```bash
bdx-prototype-ioc
```

Run only the default PSU IOC:

```bash
bdx-psu-ioc
```

The default PV-list and display-generation commands also use
`config/profiles/default` unless `--config-dir` is supplied:

```bash
bdx-pv-list
bdx-generate-displays --output-dir phoebus/displays --only psu
```

## Run the simulated IOC

```bash
./scripts/run_all_simulated.sh
```

This uses the full simulated profile in `config/profiles/prototype`.

The default simulation update period is 1 second, corresponding to 1 Hz. It can be changed at runtime between 1 and 3600 seconds:

```bash
caproto-put BDX:GLOBAL:UPDATE_PERIOD_SET 1
caproto-get BDX:GLOBAL:UPDATE_FREQUENCY_RBV
```

The minimum period of 1 second limits the prototype update frequency to 1 Hz.

## Raspberry MCP9808 temperature IOC

The Raspberry Pi should run only the environment IOC because the MCP9808 sensors are
attached to its local I2C bus. See `docs/raspberry.md` for the deployment procedure.

The Raspberry configuration source is `config/profiles/raspberry/environment.json`.
The verified hardware uses Raspberry Pi 4B BSC6 on GPIO22/GPIO23, Linux device
`/dev/i2c-6`, and MCP9808 addresses `0x18` through `0x1B`. The Raspberry boot
configuration must include:

```text
dtoverlay=i2c6,pins_22_23,baudrate=10000
```

It exposes:

```text
BDX:ENV:TEMP:T00:VALUE
BDX:ENV:TEMP:T01:VALUE
BDX:ENV:TEMP:T02:VALUE
BDX:ENV:TEMP:T03:VALUE
```

Do not run another environment IOC on the main server when the Raspberry is active.
Two Channel Access servers must never expose the same PV names.

The Raspberry uses a dedicated slow-control Ethernet address, `172.22.50.10/24`,
on `eth0`. This Ethernet profile has no gateway, no DNS, and no default route;
Wi-Fi remains available for administration, Internet access, and the default route.
Wi-Fi credentials are intentionally not stored in this repository.

Deploy the Raspberry network and IOC from the repository:

```bash
sudo ./scripts/configure_raspberry_network.sh
sudo ./scripts/install_raspberry.sh pi
```

Network configuration is a separate explicit step because changing NetworkManager
profiles can affect connectivity. The IOC binds to `172.22.50.10` through the
repository-controlled `config/profiles/raspberry/bdx.env`, installed as
`/etc/bdx-slow-control/bdx.env`.

On the Raspberry, check the installed hardware configuration before starting the IOC:

```bash
/opt/bdx-slow-control/.venv/bin/bdx-environment-check \
  --config /etc/bdx-slow-control/profiles/raspberry/environment.json
```

## Command-line test

From a second terminal:

```bash
source .venv/bin/activate

caproto-get BDX:GLOBAL:SYSTEM_STATE
caproto-get BDX:ENV:TEMP:T01:VALUE
caproto-put BDX:PSU:LV1:CH1:VOLTAGE_REQUEST 5
caproto-put BDX:PSU:LV1:CH1:CURRENT_LIMIT_REQUEST 0.5
caproto-put BDX:PSU:LV1:CH1:APPLY_CMD 1
```

When EPICS Base is configured in the shell, `caget`, `caput`, `cainfo`, and `camonitor` can be used instead.

For the Raspberry temperature IOC, point Channel Access clients at the Raspberry:

```bash
export EPICS_CA_ADDR_LIST=172.22.50.10
export EPICS_CA_AUTO_ADDR_LIST=NO
caproto-get BDX:ENV:TEMP:T00:VALUE
```

## EPICS Archiver Appliance

BDX-specific Archiver Appliance deployment infrastructure lives in
`deploy/archiver-appliance`. It contains only repository-owned scripts,
templates, checksums, policies, PV lists, systemd examples, documentation, and
offline tests. It does not store downloaded Archiver Appliance releases, Tomcat
runtimes, WAR/JAR files, credentials, logs, databases, or archive data.

See `deploy/archiver-appliance/README.md` for the pinned release, storage
layout, local evaluation commands, provisional Ubuntu 22.04 deployment commands,
PV registration, health checks, retrieval tests, and backup procedure.

## Phoebus displays

The repository includes:

```text
phoebus/displays/overview.bob
phoebus/displays/psu.bob
phoebus/displays/psu_expert.bob
phoebus/displays/chiller.bob
phoebus/displays/chiller_expert.bob
phoebus/displays/environment.bob
phoebus/displays/environment_expert.bob
phoebus/displays/hv.bob
phoebus/displays/daq.bob
phoebus/displays/global.bob
phoebus/displays/trends.bob
phoebus/displays/all_pvs.bob
phoebus/displays/pv_list.pvs
```

`psu.bob` and `chiller.bob` are operator-oriented pages. They show large live
readbacks, communication LEDs, confirmed output/run commands, staged request
fields, apply buttons, and the principal live Data Browser plots. Complete PV
tables and direct compatibility controls live in `psu_expert.bob` and
`chiller_expert.bob`.

`all_pvs.bob` contains every configured PV for the selected generation profile.
Every writable PV has an appropriate text entry or command button.

The global and overview displays provide:

- simulated interlock trigger;
- interlock reset;
- global all-off with confirmation;
- live update-period selection;
- update-frequency readback.

The trend widgets subscribe to live Channel Access updates. Generated Data
Browser plots use a moving time window ending at `now`; set `BDX_TREND_RANGE`,
`BDX_TREND_SCAN_PERIOD`, and `BDX_TREND_RING_SIZE` when regenerating displays to
change the default range, live scan period, and live buffer size. The default
trace scan period is 1.0 s. Set `BDX_TREND_ARCHIVE_REQUEST=OPTIMIZED` before
regeneration when long historical ranges should prefer optimized Archiver
Appliance retrieval.

By default, generated plots include the local BDX Archiver Appliance retrieval
source at `http://127.0.0.1:17668/retrieval`. Set
`BDX_ARCHIVER_ENABLED=false` before generation when live-only `.plt` files are
needed. Data Browser combines historical pbraw retrieval with the live Channel
Access buffer when the PV is registered and the retrieval service is available.
Operator pages embed the principal plots and provide “Full history” actions
that open the corresponding Data Browser `.plt` resource. “Full history” is
available only for PVs that are registered and actively sampled by Archiver
Appliance; history cannot be reconstructed retroactively for periods before a
PV was archived.

PSU operator plots contain one dual-axis Data Browser plot per physical LV
supply. Each plot shows actual channel voltage readbacks on the voltage axis
and actual channel current readbacks on the current axis. Chiller operator
plots show controlled temperature, bath temperature, and applied setpoint;
optional pressure or external-temperature plots are generated only when those
measurements are enabled in the selected profile.

## Configure the Phoebus launcher

Phoebus itself is not bundled with this repository. Copy the local environment template:

```bash
cp phoebus/phoebus.env.example phoebus/phoebus.env
```

Then edit `phoebus/phoebus.env`. For the development layout where `bdx-slow-control` and `preliminary_test_epics` are sibling directories:

```bash
BDX_PHOEBUS_CMD="../preliminary_test_epics/phoebus/phoebus-product/phoebus.sh"
```

You may instead export an absolute path:

```bash
export BDX_PHOEBUS_CMD=/path/to/phoebus-product/phoebus.sh
```

The launcher also recognizes:

```text
BDX_PHOEBUS_HOME
BDX_PHOEBUS_APP
BDX_CA_ADDR_LIST
BDX_CA_AUTO_ADDR_LIST
BDX_CA_SERVER_PORT
BDX_CA_REPEATER_PORT
BDX_PHOEBUS_UPDATE_THROTTLE_MS
BDX_TREND_RANGE
BDX_TREND_SCAN_PERIOD
BDX_TREND_RING_SIZE
BDX_TREND_ARCHIVE_REQUEST
BDX_PHOEBUS_DISPLAY
BDX_ARCHIVER_ENABLED
BDX_ARCHIVER_URL
BDX_ARCHIVER_NAME
BDX_ARCHIVER_STRICT_CHECK
BDX_ARCHIVER_PREFLIGHT_PV
```

Relative launcher paths are resolved first from the current working directory and then from the repository root.

For the deployed two-host layout, configure Phoebus with both Channel Access servers:

```bash
BDX_CA_ADDR_LIST="<MAIN_SERVER_IP> <RASPBERRY_IP>"
BDX_CA_AUTO_ADDR_LIST=false
```

## Launch Phoebus

With the IOC already running:

```bash
./scripts/launch_phoebus.sh
```

Open a specific display:

```bash
./scripts/launch_phoebus.sh psu
./scripts/launch_phoebus.sh trends
./scripts/launch_phoebus.sh all_pvs
```

Start the IOC when needed and launch Phoebus in one command:

```bash
./scripts/run_prototype_with_phoebus.sh
```

The launcher creates a runtime `settings.ini` containing the local Channel Access address list and Phoebus display update throttle. The default throttle is 1000 ms, or at most 1 Hz.

Launch with live Channel Access plots only:

```bash
BDX_ARCHIVER_ENABLED=false ./scripts/launch_phoebus.sh overview
```

Launch with the default local EPICS Archiver Appliance retrieval endpoint:

```bash
./scripts/launch_phoebus.sh overview
```

Launch with a non-default EPICS Archiver Appliance retrieval endpoint:

```bash
BDX_ARCHIVER_ENABLED=true \
BDX_ARCHIVER_URL=http://<ARCHIVER_HOST>:17668/retrieval \
BDX_ARCHIVER_NAME="BDX Archiver" \
./scripts/launch_phoebus.sh overview
```

The launcher writes Phoebus Data Browser archive preferences using pbraw syntax.
It does not print credentials. If retrieval is temporarily unavailable, Phoebus
continues to use live Channel Access data unless `BDX_ARCHIVER_STRICT_CHECK=true`
and the optional preflight check fails.

## Regenerate displays after configuration changes

```bash
source .venv/bin/activate
bdx-generate-displays --output-dir phoebus/displays
```

The command above uses `config/profiles/default`. Regenerate the full simulated
prototype displays explicitly when needed:

```bash
bdx-generate-displays \
  --config-dir config/profiles/prototype \
  --output-dir phoebus/displays
```

To update only the deployed Raspberry environment display on a laptop without
overwriting unrelated displays:

```bash
bdx-generate-displays \
  --config-dir config/profiles/raspberry \
  --output-dir phoebus/displays \
  --only environment
```

To update only the deployed PSU display or the not-yet-default main-server
chiller display:

```bash
bdx-generate-displays \
  --config-dir config/profiles/default \
  --output-dir phoebus/displays \
  --only psu

bdx-generate-displays \
  --config-dir config/profiles/main-server \
  --output-dir phoebus/displays \
  --only chiller
```

The generator reads the actual caproto PV database. It also creates a valid Phoebus PV Table file using the `<pvtable version="3.0">` format.

To override the embedded Archiver Appliance source:

```bash
BDX_ARCHIVER_ENABLED=true \
BDX_ARCHIVER_URL=http://<ARCHIVER_HOST>:17668/retrieval \
bdx-generate-displays \
  --config-dir config/profiles/default \
  --output-dir phoebus/displays \
  --only psu
```

Register the same PVs through the deployment helper and leave them actively
sampled before relying on historical data. The Archiver Appliance cannot
reconstruct samples from before a PV was registered and archived:

```bash
deploy/archiver-appliance/scripts/register-pvs.py \
  --mgmt-url http://<ARCHIVER_HOST>:17665/mgmt/bpl \
  deploy/archiver-appliance/pv-lists/environment.txt \
  deploy/archiver-appliance/pv-lists/psu.txt \
  deploy/archiver-appliance/pv-lists/chiller.txt
```

## Automated validation

```bash
pytest
```

The tests check:

- driver behavior;
- PV naming and aggregation;
- runtime update-frequency limits;
- XML validity of every `.bob` file;
- coverage of every configured PV in `all_pvs.bob`;
- presence of a control widget for every writable PV.

## Network interface

Set the Channel Access server interface explicitly when the host has multiple network interfaces:

```bash
export BDX_EPICS_INTERFACE=193.206.147.141
```

For a local Phoebus client, the default client address list is `127.0.0.1`. For the two-host deployment, set `BDX_CA_ADDR_LIST` to both IOC host addresses:

```bash
BDX_CA_ADDR_LIST="<MAIN_SERVER_IP> <RASPBERRY_IP>"
BDX_CA_AUTO_ADDR_LIST=false
```

Do not use a client address list that can discover two servers publishing the same PV names.

## Single-host and split-host deployment

The prototype serves all modular IOC groups from one caproto server process. This avoids ambiguous Channel Access UDP search handling when several independent caproto servers share one host and interface.

Individual subsystem commands remain available for focused driver development and future distribution across separate hosts.
