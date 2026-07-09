# GNU Screen Operator Launchers

The Ubuntu operator host provides three user commands:

```bash
launch-bdx-slow-control
launch-bdx-phoebus
start-bdx-raspberry-ioc
```

Install or refresh the commands after cloning or pulling repository changes:

```bash
cd ~/SlowControl/app/bdx-slow-control
bash scripts/install_user_commands.sh
```

The installer refreshes the editable package installation and creates links in
`~/.local/bin`. GNU Screen must be installed and `~/.local/bin` must be in
`PATH`.

## Slow-control session

```bash
launch-bdx-slow-control
```

Before creating the local Screen session, the launcher checks the Raspberry
readiness PV:

```text
BDX:ENV:TEMP:T00:VALUE
```

The check uses Channel Access directed only to `172.22.50.10`. When the PV does
not respond, the launcher prints:

```text
Start it with: start-bdx-raspberry-ioc
```

The missing Raspberry IOC does not prevent the main IOC and Archiver from
starting; it is reported as an explicit degraded condition.

The command creates the detached Screen session `bdx-slow-control` with two
windows:

```text
archiver  starts or validates the user-local Archiver Appliance deployment
ioc       runs bdx-prototype-ioc on BDX_MAIN_HOST
```

`BDX_MAIN_HOST` is read from the environment or from the untracked
`config/runtime.env`. The launcher refuses to start a second IOC when a Channel
Access server is already listening on `$BDX_MAIN_HOST:5064` outside the managed
Screen session. Stop the existing foreground IOC before the first migration to
Screen.

The Archiver window is idempotent: it leaves a complete running deployment
untouched, starts a fully inactive deployment, and refuses to create duplicate
processes when only part of the appliance is active. It waits for the repository
health check before leaving an interactive shell in the window. The Archiver
Tomcat processes continue in the background independently of Screen.

Attach from either the local terminal or SSH as the same Unix user:

```bash
screen -x bdx-slow-control
```

Inside Screen, use `Ctrl-A N` for the next window, `Ctrl-A P` for the previous
window, and `Ctrl-A D` to detach without stopping anything. Pass `--attach` to
attach immediately after launching:

```bash
launch-bdx-slow-control --attach
```

## Raspberry environment IOC

Start the installed Raspberry systemd service remotely and verify its readiness
PV from the Ubuntu host:

```bash
start-bdx-raspberry-ioc
```

The default SSH destination is:

```text
pi@172.22.50.10
```

The command is idempotent. When `BDX:ENV:TEMP:T00:VALUE` already responds, it
returns without invoking SSH. Otherwise it runs:

```text
sudo systemctl start bdx-environment-ioc
sudo systemctl --no-pager --full status bdx-environment-ioc
```

and waits up to 30 seconds for the Channel Access PV. SSH authentication and any
required `sudo` password remain interactive. The Raspberry must already contain
the service installed by `scripts/install_raspberry.sh`; this command does not
install software, configure networking, or power on a switched-off Raspberry.

Override the SSH destination when the administration address or username is
different:

```bash
start-bdx-raspberry-ioc --ssh-host USER@HOST
```

The same value can be made persistent in the shell environment:

```bash
export BDX_RASPBERRY_SSH_HOST=USER@HOST
```

Force a service restart and wait longer for the PV when diagnosing the remote
IOC:

```bash
start-bdx-raspberry-ioc --restart --timeout 60
```

## Phoebus session

Run the Phoebus command from a terminal opened in the Ubuntu graphical session:

```bash
launch-bdx-phoebus
```

The command creates the detached Screen session `bdx-phoebus`, launches the
`overview` display, enables Archiver history, and performs a strict retrieval
preflight using `BDX:ENV:TEMP:T00:VALUE`.

The first launch requires a valid `DISPLAY`. Once Phoebus is running, its Screen
terminal and logs can be inspected over SSH:

```bash
screen -x bdx-phoebus
```

The GUI remains on the Ubuntu graphical display; attaching from SSH does not
move the window to the remote computer. Closing Phoebus terminates its dedicated
Screen session.

The default Phoebus installation is:

```text
~/SlowControl/css/phoebus-4.7.4-SNAPSHOT
```

Override it with `BDX_PHOEBUS_HOME` or `--phoebus-home`. A different generated
BOB display can be passed as the positional argument:

```bash
launch-bdx-phoebus psu
```

## Controlled shutdown

Use the repository shutdown commands before removing the Screen sessions:

```bash
./scripts/kill_slow_control_phoebus.sh
./scripts/kill_slow_control_archiver.sh
./scripts/kill_slow_control_ioc.sh
```

After the managed processes have stopped, stale Screen shells can be closed with:

```bash
screen -S bdx-slow-control -X quit
screen -S bdx-phoebus -X quit
```
