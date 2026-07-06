# Architecture

## Scope

The prototype host should reproduce the final detector control model at reduced scale. The relevant target is behavioral compatibility, not device count.

The prototype should exercise:

- independent subsystem IOCs;
- stable PV naming;
- setpoint and readback separation;
- communication status and stale-data handling;
- simulated fault injection;
- global state and interlock commands;
- DAQ crate configuration and readiness;
- service supervision;
- alarm and archive integration.

## Runtime model

Each subsystem is implemented as an independent IOC group and driver module.

For the single prototype host, the validated default is:

```text
bdx-prototype-ioc
├── PSU IOC group
├── chiller IOC group
├── environment IOC groups
├── HV IOC group
├── DAQ IOC group
└── global IOC group
```

This preserves code and PV modularity while using one Channel Access search server on the host.

The individual commands remain available for focused development and for future split-host deployment:

```text
bdx-psu-ioc
bdx-chiller-ioc
bdx-environment-ioc
bdx-hv-ioc
bdx-daq-ioc
bdx-global-ioc
```

Independent process isolation can be restored when subsystems are assigned to different hosts or when a deliberate Channel Access search-port strategy is introduced.

## Layering

```text
Hardware or simulator
        |
Driver interface
        |
EPICS IOC
        |
Channel Access
        |
Phoebus / alarm server / archiver / DAQ coordination
```

The IOC must not contain transport-specific device logic. Hardware communication belongs in the driver layer.

## Configuration

JSON files define:

- PV prefix;
- simulation or hardware mode;
- device count and channel count;
- polling period;
- initial simulated state;
- server interface.

No IP address, TCP port, or channel count should be hard-coded in an IOC class.

## PV naming

The default hierarchy is:

```text
BDX:<SUBSYSTEM>:<DEVICE>:<CHANNEL>:<FIELD>
```

Examples:

```text
BDX:PSU:LV1:CH1:VOLTAGE_SET
BDX:PSU:LV1:CH1:VOLTAGE_RBV
BDX:ENV:TEMP:T01:VALUE
BDX:DAQ:CRATE01:READY
BDX:GLOBAL:INTERLOCK_ACTIVE
```

Command PVs use `_CMD`. Direct compatibility setpoint writes use `_SET`.
Operator-staged values use `_REQUEST` and are applied through an explicit
command PV. Device readbacks use `_RBV`.

## Setpoint/readback rule

A successful write to a setpoint PV means that the command was accepted by the IOC. The corresponding readback is updated only after the driver reports the device state.

Operator displays should prefer staged request/apply PVs when changing hardware
settings. Direct write PVs may remain available in expert displays for
compatibility, but they must not be displayed as hardware readbacks.

## Error model

Every managed IOC exposes:

```text
HEARTBEAT
IOC_STATE
COMM_STATUS
COMM_OK
LAST_UPDATE
ERROR_CODE
ERROR_MESSAGE
SIMULATION
CLEAR_ERROR_CMD
```

`COMM_OK` is a read-only boolean suitable for Phoebus LEDs. It is false before
the first successful communication, true after successful polling, and false
after communication or device failures.

The baseline communication states are:

```text
STARTING
OK
DISCONNECTED
TIMEOUT
INVALID_DATA
DEVICE_ERROR
DISABLED
SIMULATION
```

## Interlocks

Safety logic must not depend on Phoebus.

The initial `global` IOC exposes the global state and command surface. Cross-IOC subscriptions and action propagation are deliberately left as the next implementation step because the final rules must be defined from detector requirements.

A power interlock should normally latch and require an explicit reset.

## Compatibility

This repository does not preserve the execution model or CLI of `test_ioc.py`. Existing hardware driver code may be ported behind the new interfaces, but the old IOC structure should not constrain this design.

## Shared update timing

The aggregated prototype uses one `RuntimeSettings` instance shared by every managed IOC group. The global IOC exposes:

```text
BDX:GLOBAL:UPDATE_PERIOD_SET
BDX:GLOBAL:UPDATE_PERIOD_RBV
BDX:GLOBAL:UPDATE_FREQUENCY_RBV
BDX:GLOBAL:MIN_UPDATE_PERIOD_RBV
BDX:GLOBAL:MAX_UPDATE_PERIOD_RBV
```

The prototype range is 1–3600 seconds. This keeps the live update frequency at or below 1 Hz while allowing the operator to change it without restarting the IOC.

## Simulated interlock coordination

The prototype build context registers the PSU and HV `all_off` callbacks before constructing the global IOC. `INTERLOCK_TEST_CMD` and `ALLOFF_CMD` therefore switch the simulated power outputs off. Resetting the interlock clears the latched global state but does not automatically restore power outputs.

## Phoebus display generation

`bdx_slow_control.phoebus_generator` reads the actual configured caproto PV database and generates the complete Display Builder set. This keeps controls synchronized with future additions to the PV contract.

Generated subsystem navigation opens displays in a new Phoebus tab. PSU and
chiller operator pages are intentionally not complete PV tables; their expert
pages contain the full subsystem PV tables and direct compatibility controls.
