# Hardware Drivers

Place production hardware implementations in this package.

Recommended modules:

```text
cpx400dp.py
chiller_<model>.py
hv_<model>.py
mcp9808.py
daq_crate.py
```

Each implementation must preserve the interfaces declared in `drivers/base.py`.

## MCP9808 temperature sensors

`mcp9808.py` reads MCP9808 sensors through the Linux `i2c-dev` interface
(`/dev/i2c-*`). It does not use Raspberry Pi register mappings, `/dev/mem`,
or GPIO triggers.

Use `mode: "hardware"` and `driver: "mcp9808"` in an environment sensor
configuration. See `config/profiles/raspberry/environment.json`.

## TTi CPX400DP power supplies

`cpx400dp.py` controls one dual-channel CPX400DP low-voltage power supply through its
TCP/IP ASCII interface. It uses the legacy command set found in
`legacy_software/power_supply/python_script/CPX400DP.py`.

Use `mode: "hardware"` and `driver: "cpx400dp"` in a PSU device
configuration. The main-server profile contains two hardware devices:

```text
BDX:PSU:LV1: -> 192.168.1.100:9221
BDX:PSU:LV2: -> 169.254.23.187:9221
```

The driver does not disable outputs during IOC startup. The EPICS
`ALLOFF_CMD` PV sends `OPALL 0` to the selected physical supply when an
operator explicitly requests an all-off action.

## TDK-Lambda GENH600 high-voltage supplies

`genh600.py` controls one single-output GENH600 high-voltage supply through
its serial ASCII interface. It uses the legacy command set found in
`legacy_software/power_supply/python_script/GENH600.py`.

Use `mode: "hardware"` and `driver: "genh600"` in an HV device
configuration. The main-server profile contains one hardware device:

```text
BDX:HV:HV1: -> /dev/ttyUSB0, address 6, 9600 baud
```

The driver does not reset the supply or clear programmed voltage/current
setpoints during IOC startup. The EPICS `ALLOFF_CMD` PV sends `OUT 0` when an
operator explicitly requests an all-off action.

## LAUDA ECO Silver RE 1225 S chiller

`ecosilver_re_1225s.py` controls the LAUDA chiller through its TCP/IP ASCII
interface. It uses the command set found in
`legacy_software/chiller/python_script/ECOSILVER_RE_1225S.py`.

Use `mode: "hardware"` and `driver: "ecosilver_re_1225s"` in a chiller device
configuration. The main-server profile contains one hardware device:

```text
BDX:CHILLER:CHILLER1: -> 192.168.1.2:54321
```

The driver does not start, stop, reset, or put the chiller into Safe Mode
during IOC startup. `RUN_SET=1` sends `START`; `RUN_SET=0` sends `STOP`.
`SETPOINT_SET` sends `OUT_SP_00_<value>`.
