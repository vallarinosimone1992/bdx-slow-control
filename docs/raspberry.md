# Raspberry MCP9808 Environment IOC

This deployment runs only the environmental MCP9808 IOC on a Raspberry Pi. The IOC
command is `bdx-environment-ioc`, and the installed configuration is
`/etc/bdx-slow-control/profiles/raspberry/environment.json`.

The repository source configuration is `config/profiles/raspberry/environment.json`. It uses:

- Raspberry Pi 4B BSC6 on GPIO22/GPIO23;
- I2C device `/dev/i2c-6`;
- MCP9808 addresses `0x18`, `0x19`, `0x1A`, and `0x1B`;
- a 5 second polling interval;
- `BDX_EPICS_INTERFACE` as the optional server interface override.

## Install

Copy or clone the repository on the Raspberry Pi, then run the installer with sudo.
Pass the runtime user explicitly, or omit it to use `SUDO_USER`.

```bash
cd /path/to/bdx-slow-control
sudo ./scripts/install_raspberry.sh <runtime-user>
```

Example when logged in as the runtime user:

```bash
cd /path/to/bdx-slow-control
sudo ./scripts/install_raspberry.sh
```

The installer copies the application to `/opt/bdx-slow-control`, creates
`/opt/bdx-slow-control/.venv`, installs the Python package, installs only the Raspberry
profile as `/etc/bdx-slow-control/profiles/raspberry/`, and renders
`/etc/systemd/system/bdx-environment-ioc.service`.

It does not enable or start the service automatically. It also does not modify
`/boot/firmware/config.txt`; update the boot overlay manually as described below.

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

## Configure Interface

Edit `/etc/bdx-slow-control/bdx.env` if the Raspberry has multiple network interfaces.
For Raspberry address `10.0.2.133`:

```bash
sudo nano /etc/bdx-slow-control/bdx.env
```

Set:

```text
BDX_EPICS_INTERFACE=10.0.2.133
BDX_LOG_LEVEL=INFO
```

If `BDX_EPICS_INTERFACE` is not set, the JSON default binds the IOC to `0.0.0.0`.

## Diagnostic Check

Run the environment diagnostic as the same runtime user used by the systemd service:

```bash
sudo -u <runtime-user> /opt/bdx-slow-control/.venv/bin/bdx-environment-check \
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

## Manual Test

After diagnostics succeed, test the IOC directly:

```bash
sudo -u <runtime-user> /opt/bdx-slow-control/.venv/bin/bdx-environment-ioc \
  --config /etc/bdx-slow-control/profiles/raspberry/environment.json
```

From another terminal or client host:

```bash
export EPICS_CA_ADDR_LIST=10.0.2.133
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
