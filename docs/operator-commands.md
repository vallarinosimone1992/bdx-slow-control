# Ubuntu Operator Commands

The Ubuntu slow-control host exposes one graphical startup command and direct
shutdown commands for the three local components.

Install or refresh the commands after pulling repository changes:

```bash
cd ~/SlowControl/app/bdx-slow-control
bash scripts/install_user_commands.sh
```

The installer refreshes the editable Python installation and creates command
links under `~/.local/bin`.

## Start the local slow-control stack

Run this command from a terminal opened in the Ubuntu graphical desktop session:

```bash
bdx_slow_control_start
```

The command checks the Raspberry environment IOC, then opens two independent
terminal windows:

```text
BDX Main IOC
    runs bdx-prototype-ioc on BDX_MAIN_HOST

BDX Archiver and Phoebus
    waits for the main IOC
    starts or validates the user-local Archiver Appliance
    waits for an archived PV connection
    launches Phoebus with Archiver history enabled
```

GNU Screen is not used. The IOC terminal remains directly visible to the expert
operator. Phoebus is launched from the graphical desktop environment, so JavaFX
can access the Ubuntu display.

`BDX_MAIN_HOST` is read from the environment or from the untracked
`config/runtime.env`. The operational value on the current prototype host is:

```text
BDX_MAIN_HOST=172.22.50.2
```

The command is idempotent:

- if a Channel Access server is already listening on `BDX_MAIN_HOST:5064`, no
  second main IOC is opened;
- if the recorded Phoebus process is still running, no second Phoebus instance
  is opened;
- a healthy Archiver deployment is left untouched;
- a partially running Archiver deployment is reported instead of duplicated.

The default display is `overview`. A different generated display can be passed
as the positional argument:

```bash
bdx_slow_control_start psu
```

The default Phoebus installation is:

```text
~/SlowControl/css/phoebus-4.7.4-SNAPSHOT
```

Override it with:

```bash
bdx_slow_control_start --phoebus-home /path/to/phoebus
```

## Raspberry environment IOC check

Before opening local terminals, the command verifies:

```text
BDX:ENV:TEMP:T00:VALUE
```

using Channel Access directed only to `172.22.50.10`. If the PV does not
respond, startup continues in degraded mode and prints:

```text
Start it with: start-bdx-raspberry-ioc
```

Start the installed Raspberry systemd service remotely with:

```bash
start-bdx-raspberry-ioc
```

The default SSH destination is `pi@172.22.50.10`. The command does nothing when
the readiness PV already responds. Otherwise it runs `systemctl start` remotely
and waits for the PV. Use `--restart` to force a service restart.

## Direct shutdown commands

Stop the main IOC launched by `bdx_slow_control_start`:

```bash
bdx_slow_control_kill_ioc
```

Stop the user-local Archiver Appliance deployment:

```bash
bdx_slow_control_kill_archiver
```

Stop the recorded Phoebus process:

```bash
bdx_slow_control_kill_phoebus
```

The IOC and Phoebus commands use the runtime PID files under
`.runtime/bdx-stack/` and validate the recorded process before sending a signal.
The Archiver command delegates to the deployment stop script and never sends a
generic signal to unrelated Java processes.

IOC and Phoebus shutdown is graceful by default. `SIGKILL` is used only when
`--force` is supplied explicitly:

```bash
bdx_slow_control_kill_ioc --force
bdx_slow_control_kill_phoebus --force
```
