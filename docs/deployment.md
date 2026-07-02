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
cainfo BDX:PSU:PSU1:COMM_STATUS
caget BDX:PSU:PSU1:CH1:VOLTAGE_RBV
caput BDX:PSU:PSU1:CH1:VOLTAGE_SET 5.0
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
