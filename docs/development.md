# Development Notes

## Adding a new subsystem

1. Define a driver interface in `src/bdx_slow_control/drivers/base.py`.
2. Add a simulation implementation.
3. Add hardware implementation under `drivers/hardware/`.
4. Add an IOC module under `iocs/`.
5. Add a builder in `builders.py`.
6. Add a CLI entry point.
7. Add JSON configuration.
8. Add tests for the driver and PV names.

## Adding a PV

When adding a writable setting, add both:

```text
PARAMETER_SET
PARAMETER_RBV
```

The putter should call the driver and return the accepted command. The polling loop should update the readback from the driver.

## Runtime logging

Use the package logger:

```python
import logging
logger = logging.getLogger(__name__)
```

Do not write directly to arbitrary log files from IOC code. Let `systemd` or the deployment environment manage log retention.

## Hardware driver policy

Hardware driver methods are synchronous in this template. If a device API blocks for a significant amount of time, move I/O to a worker thread or implement an asynchronous driver without changing the IOC-facing semantics.

## Regenerating the Phoebus contract

After adding, removing, or renaming PVs:

```bash
bdx-generate-displays --config-dir config/profiles/prototype --output-dir phoebus/displays
pytest
```

The display tests ensure that every configured PV appears in `all_pvs.bob` and that every writable PV has a control widget.
