# Generated Displays

These files are generated from the configured caproto PV database. Do not manually edit large generated tables unless the same change is implemented in `bdx_slow_control.phoebus_generator`.

Regenerate with:

```bash
bdx-generate-displays \
  --config-dir config/profiles/prototype \
  --output-dir phoebus/displays
```

The deployed main-server PSU and chiller displays can be regenerated without
overwriting unrelated subsystem displays:

```bash
bdx-generate-displays \
  --config-dir config/profiles/main-server \
  --output-dir phoebus/displays \
  --only psu

bdx-generate-displays \
  --config-dir config/profiles/main-server \
  --output-dir phoebus/displays \
  --only chiller
```

Data Browser `.plt` files use a live moving time window:

```text
start = -10 minutes
end = now
```

Set `BDX_TREND_RANGE` and `BDX_TREND_SCAN_PERIOD` before regeneration to change
the generated window and live sample period. The default generated trace scan
period is 1.0 s. Set `BDX_TREND_RING_SIZE` to change
the live Data Browser ring buffer. Set `BDX_TREND_ARCHIVE_REQUEST=OPTIMIZED`
before regeneration for long historical windows where optimized Archiver
Appliance retrieval is preferred; the default request is `RAW`.

By default, generated `.plt` files contain live Channel Access traces without
archive sources:

```bash
BDX_ARCHIVER_ENABLED=false bdx-generate-displays \
  --config-dir config/profiles/prototype \
  --output-dir phoebus/displays
```

To embed an EPICS Archiver Appliance retrieval source in generated `.plt` files:

```bash
BDX_ARCHIVER_ENABLED=true \
BDX_ARCHIVER_URL=http://<ARCHIVER_HOST>:17668/retrieval \
BDX_ARCHIVER_NAME="BDX Archiver" \
bdx-generate-displays \
  --config-dir config/profiles/main-server \
  --output-dir phoebus/displays \
  --only psu
```

Phoebus uses `pbraw://` archive URLs for Archiver Appliance retrieval. The
launcher can also provide the archive source through runtime preferences, so
the checked-in plots may remain live-only while a deployed Phoebus session uses
historical data.

`psu.bob` and `chiller.bob` are operator pages. Complete subsystem PV tables
are generated in `psu_expert.bob` and `chiller_expert.bob`.

Operator pages embed the principal Data Browser plots and include “Full
history” actions that open the corresponding `.plt` resource. Full history
requires the PV to be registered and actively sampled by Archiver Appliance;
samples cannot be reconstructed retroactively. PSU plots use one
dual-axis plot per physical LV supply. Chiller plots include controlled
temperature, bath temperature, and applied setpoint. Chiller pressure and
external-temperature plots are generated only when those measurements are
enabled in the selected profile.
