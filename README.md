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

## Repository layout

```text
bdx-slow-control/
├── config/
│   ├── profiles/           Installable IOC configuration profiles
│   └── examples/           Non-installed configuration examples
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

## Run the simulated IOC

```bash
./scripts/run_all_simulated.sh
```

This uses the full simulated profile in `config/profiles/prototype`.

The default simulation update period is 5 seconds, corresponding to 0.2 Hz. It can be changed at runtime between 2 and 3600 seconds:

```bash
caproto-put BDX:GLOBAL:UPDATE_PERIOD_SET 2
caproto-get BDX:GLOBAL:UPDATE_FREQUENCY_RBV
```

The minimum period of 2 seconds limits the prototype update frequency to 0.5 Hz.

## Raspberry MCP9808 temperature IOC

The Raspberry Pi should run only the environment IOC because the MCP9808 sensors are
attached to its local I2C bus. See `docs/raspberry.md` for the deployment procedure.

The Raspberry configuration source is `config/profiles/raspberry/environment.json`. It exposes:

```text
BDX:ENV:TEMP:T00:VALUE
BDX:ENV:TEMP:T01:VALUE
BDX:ENV:TEMP:T02:VALUE
BDX:ENV:TEMP:T03:VALUE
```

Do not run another environment IOC on the main server when the Raspberry is active.
Two Channel Access servers must never expose the same PV names.

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
caproto-put BDX:PSU:PSU1:CH1:VOLTAGE_SET 5
caproto-put BDX:PSU:PSU1:CH1:OUTPUT_SET 1
```

When EPICS Base is configured in the shell, `caget`, `caput`, `cainfo`, and `camonitor` can be used instead.

For the Raspberry temperature IOC, point Channel Access clients at the Raspberry:

```bash
export EPICS_CA_ADDR_LIST=10.0.2.133
export EPICS_CA_AUTO_ADDR_LIST=NO
caproto-get BDX:ENV:TEMP:T00:VALUE
```

## Phoebus displays

The repository includes:

```text
phoebus/displays/overview.bob
phoebus/displays/psu.bob
phoebus/displays/chiller.bob
phoebus/displays/environment.bob
phoebus/displays/hv.bob
phoebus/displays/daq.bob
phoebus/displays/global.bob
phoebus/displays/trends.bob
phoebus/displays/all_pvs.bob
phoebus/displays/pv_list.pvs
```

`all_pvs.bob` contains every configured simulation PV. Every writable PV has an appropriate text entry, boolean button, or command button. The subsystem displays provide the same controls with subsystem-specific trends and quick actions.

The global and overview displays provide:

- simulated interlock trigger;
- interlock reset;
- global all-off with confirmation;
- live update-period selection;
- update-frequency readback.

The trend widgets subscribe to live Channel Access updates. Their sampling cadence follows `BDX:GLOBAL:UPDATE_PERIOD_SET`. Their time range can be adjusted from the plot toolbar or by setting `BDX_TREND_RANGE` before launching Phoebus.

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
BDX_PHOEBUS_DISPLAY
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

The launcher creates a runtime `settings.ini` containing the local Channel Access address list and Phoebus display update throttle. The default throttle is 2000 ms, or at most 0.5 Hz.

## Regenerate displays after configuration changes

```bash
source .venv/bin/activate
bdx-generate-displays --config-dir config/profiles/prototype --output-dir phoebus/displays
```

The generator reads the actual caproto PV database. It also creates a valid Phoebus PV Table file using the `<pvtable version="3.0">` format.

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
