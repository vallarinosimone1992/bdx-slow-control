# Raspberry MCP9808 Environment IOC

This deployment runs only the environmental MCP9808 IOC on a Raspberry Pi. The IOC
command is `bdx-environment-ioc`, and the installed configuration is
`/etc/bdx-slow-control/profiles/raspberry/environment.json`.

The repository is the canonical source for both the Raspberry IOC runtime
environment and the dedicated slow-control Ethernet profile:

- IOC configuration: `config/profiles/raspberry/environment.json`;
- IOC runtime environment: `config/profiles/raspberry/bdx.env`;
- Ethernet configuration: `config/deployment/raspberry-network.env`.

The Raspberry environment profile uses:

- Raspberry Pi 4B BSC6 on GPIO22/GPIO23;
- I2C device `/dev/i2c-6`;
- MCP9808 addresses `0x18`, `0x19`, `0x1A`, and `0x1B`;
- a nominal 1.0 second monitoring period;
- `BDX_EPICS_INTERFACE=172.22.50.10`.

## Network Model

The Raspberry uses Wi-Fi for administration, Internet access, and the default
route. Wi-Fi credentials, SSIDs, passwords, DNS servers, and gateways are
intentionally not stored in this repository.

The dedicated slow-control Ethernet interface is configured separately through
NetworkManager:

```text
interface: eth0
address:   172.22.50.10/24
gateway:   none
DNS:       none
default route through eth0: disabled
```

Network configuration is intentionally separate from software installation because
changing an active network profile can affect connectivity. The software installer
does not modify NetworkManager.

## Deployment Order

On the Raspberry, clone or copy the repository, then run:

```bash
cd /path/to/bdx-slow-control
sudo ./scripts/configure_raspberry_network.sh
sudo ./scripts/install_raspberry.sh pi
```

`configure_raspberry_network.sh` creates or updates a NetworkManager Ethernet
profile named `bdx-slow-control`, binds it to `eth0`, assigns
`172.22.50.10/24`, disables default routing through `eth0`, and leaves Wi-Fi
profiles untouched.

`install_raspberry.sh` copies the application to `/opt/bdx-slow-control`, creates
`/opt/bdx-slow-control/.venv`, installs the Python package, installs only the
Raspberry IOC JSON profile under `/etc/bdx-slow-control/profiles/raspberry/`,
installs `config/profiles/raspberry/bdx.env` as `/etc/bdx-slow-control/bdx.env`,
and renders `/etc/systemd/system/bdx-environment-ioc.service`.

The installer does not enable or start the service automatically. It also does not
modify `/boot/firmware/config.txt`; update the boot overlay manually as described
below.

## Check I2C

Enable I2C on the Raspberry Pi before starting the IOC. The expected Linux device is
`/dev/i2c-6`.

The Raspberry boot configuration must contain this overlay in
`/boot/firmware/config.txt`:

```text
dtoverlay=i2c6,pins_22_23,baudrate=10000
```

Reboot the Raspberry Pi after adding or changing the overlay:

```bash
sudo reboot
```

After reboot, list I2C adapters and confirm that bus 6 is present:

```bash
i2cdetect -l
```

Use `i2cdetect` to confirm that the configured MCP9808 addresses answer on bus 6:

```bash
sudo i2cdetect -y 6
```

The expected addresses are `0x18`, `0x19`, `0x1A`, and `0x1B`. `i2cdetect` is a
low-level bus check only; the IOC diagnostic below reads the same JSON configuration
used by the service.

## Diagnostic Check

Run the environment diagnostic as the same runtime user used by the systemd service:

```bash
sudo -u pi /opt/bdx-slow-control/.venv/bin/bdx-environment-check \
  --config /etc/bdx-slow-control/profiles/raspberry/environment.json
```

The command verifies that `/dev/i2c-6` exists, checks read/write access for the current
user, and reads only the configured MCP9808 addresses. It prints one line per sensor
with the sensor name, bus, address, connectivity, and temperature when readable.

Example successful output:

```text
sensor=T00 bus=/dev/i2c-6 address=0x18 connectivity=OK temperature_c=22.5625
sensor=T01 bus=/dev/i2c-6 address=0x19 connectivity=OK temperature_c=22.6250
```

Do not start the IOC service until this command exits with status code 0.

## Manual IOC Test

After diagnostics succeed, test the IOC directly:

```bash
sudo -u pi /opt/bdx-slow-control/.venv/bin/bdx-environment-ioc \
  --config /etc/bdx-slow-control/profiles/raspberry/environment.json
```

From another terminal or client host:

```bash
export EPICS_CA_ADDR_LIST=172.22.50.10
export EPICS_CA_AUTO_ADDR_LIST=NO
caproto-get BDX:ENV:TEMP:T00:VALUE
```

Stop the manual IOC with `Ctrl+C`.

## Enable Service

After the diagnostic command and the manual IOC test succeed:

```bash
sudo systemctl enable bdx-environment-ioc
sudo systemctl start bdx-environment-ioc
sudo systemctl status bdx-environment-ioc
```

## Inspect Logs

```bash
journalctl -u bdx-environment-ioc -f
```

For recent logs without following:

```bash
journalctl -u bdx-environment-ioc --since "1 hour ago"
```

## Replacement Host Procedure

To restore a fresh Raspberry Pi from the repository:

1. Clone or copy the repository to the Raspberry.
2. Configure `/boot/firmware/config.txt` with `dtoverlay=i2c6,pins_22_23,baudrate=10000`.
3. Reboot if the boot overlay was added or changed.
4. Run `sudo ./scripts/configure_raspberry_network.sh`.
5. Run `sudo ./scripts/install_raspberry.sh pi`.
6. Run the environment sensor diagnostic.
7. Enable and start `bdx-environment-ioc`.
8. Verify Channel Access from another host.

Client verification:

```bash
export EPICS_CA_ADDR_LIST=172.22.50.10
export EPICS_CA_AUTO_ADDR_LIST=NO

caproto-get BDX:ENV:TEMP:T00:VALUE
caproto-get BDX:ENV:TEMP:T01:VALUE
caproto-get BDX:ENV:TEMP:T02:VALUE
caproto-get BDX:ENV:TEMP:T03:VALUE
```
