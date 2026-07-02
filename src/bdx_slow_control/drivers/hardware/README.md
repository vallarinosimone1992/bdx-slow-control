# Hardware Drivers

Place production hardware implementations in this package.

Recommended modules:

```text
tti_cpx400dp.py
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
configuration. See `config/raspberry/environment.json`.
