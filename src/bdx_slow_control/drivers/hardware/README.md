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

`cpx400dp.py` controls one dual-channel CPX400DP low-voltage power supply
through its TCP/IP ASCII interface. It uses the official CPX400DP command set
for actual voltage/current readbacks, configured voltage/current readbacks,
output state, OVP, and OCP.

Use `mode: "hardware"` and `driver: "cpx400dp"` in a PSU device
configuration. The main-server profile contains two hardware devices:

```text
BDX:PSU:LV1: -> 172.22.50.20:9221
BDX:PSU:LV2: -> 172.22.50.21:9221
```

The driver does not disable outputs during IOC startup. The EPICS
`ALLOFF_CMD` PV sends `OPALL 0` to the selected physical supply when an
operator explicitly requests an all-off action.

The IOC enforces configurable software limits before staged operator applies.
The default CPX400DP limits are `0-60 V`, `0-20 A`, and `420 W` maximum
requested voltage-current product.

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
interface.

Use `mode: "hardware"` and `driver: "ecosilver_re_1225s"` in a chiller device
configuration. The main-server profile contains one hardware device:

```text
BDX:CHILLER:CHILLER1: -> 172.22.50.60:54321
```

The driver does not start, stop, reset, or put the chiller into Safe Mode
during IOC startup. `RUN_SET=1` sends `START`; `RUN_SET=0` sends `STOP`.
`SETPOINT_SET` sends `OUT_SP_00_<value>`.

Safe Mode is not treated as STOP. Expert Safe Mode configuration uses:

```text
OUT_SP_07 / IN_SP_07   safe setpoint
OUT_SP_08 / IN_SP_08   communication timeout
```

Pressure and external temperature are optional measurements. When disabled in
configuration, the driver does not query them and the IOC exposes them as
invalid rather than presenting cached zero values.
