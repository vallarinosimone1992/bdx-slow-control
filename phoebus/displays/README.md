# Generated Displays

These files are generated from the configured caproto PV database. Do not manually edit large generated tables unless the same change is implemented in `bdx_slow_control.phoebus_generator`.

Regenerate with:

```bash
bdx-generate-displays --config-dir config --output-dir phoebus/displays
```

All displays use the environment macro:

```text
$(BDX_TREND_RANGE=10 minutes)
```

for strip-chart history ranges.
