# Run-temperature ROOT format

`bdx_run_temperature_export <run_id> --write-root` writes one real ROOT
`TTree`, created explicitly with `mktree`, named `temperature_samples`. It does
not write an RNTuple.

## TTree schema

| Branch | ROOT/Python type | Meaning |
| --- | --- | --- |
| `sensor_id` | `int32` | Sensor identifier assigned from configured PV order |
| `timestamp_ns` | `int64` | Original lossless EPICS timestamp in nanoseconds since the Unix epoch |
| `time_from_run_start_s` | `float64` | Operational time in seconds from the CAEN run start |
| `temperature` | `float64` | Archived temperature value |
| `status` | `int32` | EPICS alarm status code or configured missing-value sentinel |
| `severity` | `int32` | EPICS alarm severity code or configured missing-value sentinel |

Entries are sorted deterministically by `timestamp_ns` and then `sensor_id`.
There is no string sensor-name branch, so the tree remains directly usable by
ROOT C++ without Awkward Arrays or complex branch types.

## Time semantics

The operational branch is `time_from_run_start_s`. Its zero is exactly the
`Start time` read from `<run_id>_info.txt`. UTC conversion is used internally
to compare the CAEN and EPICS timestamps, but UTC is not the primary analysis
coordinate.

For every sample, the writer first performs the exact integer subtraction

```text
delta_ns = timestamp_ns - run_start_timestamp_ns
```

and only then converts the result:

```text
time_from_run_start_s = delta_ns / 1_000_000_000.0
```

The value is not quantized or rounded. Samples before the run start or after
the run stop are rejected. A `float64` has ample precision for the expected run
durations and for DAQ correlations using tolerances of about 0.1 seconds.

`timestamp_ns` retains all timestamp precision supplied by EPICS. Separate
`seconds` and `nanoseconds` branches are intentionally omitted because they can
be reconstructed without loss:

```python
seconds = timestamp_ns // 1_000_000_000
nanoseconds = timestamp_ns % 1_000_000_000
```

## Sensor mapping

`sensor_id` follows the PV order in the exporter configuration. With the
standard configuration the mapping is `T00 -> 0`, `T01 -> 1`, `T02 -> 2`, and
`T03 -> 3`. The `metadata` TObjString contains an explicit mapping such as:

```json
{
  "sensor_mapping": [
    {
      "sensor_id": 0,
      "short_name": "T00",
      "pv": "BDX:ENV:TEMP:T00:VALUE"
    },
    {
      "sensor_id": 1,
      "short_name": "T01",
      "pv": "BDX:ENV:TEMP:T01:VALUE"
    }
  ]
}
```

An empty sensor remains in the mapping and has a sample count of zero, but it
does not produce a TTree entry.

## Metadata

The `metadata` object is a ROOT `TObjString` containing UTF-8 JSON. It records
the run ID; local and UTC start and stop; CAEN timezone; duration; Archiver
endpoint; sensor mapping and per-sensor counts; total sample count; histogram
bin width; package version; Git commit; exact tree schema; and time semantics.

The time-semantics object is:

```json
{
  "tree_time_semantics": {
    "relative_branch": "time_from_run_start_s",
    "relative_unit": "second",
    "relative_type": "float64",
    "relative_origin": "CAEN run start from <run_id>_info.txt",
    "relative_calculation": "(timestamp_ns - run_start_timestamp_ns) / 1e9",
    "absolute_branch": "timestamp_ns",
    "absolute_unit": "nanosecond since Unix epoch"
  }
}
```

## Histograms

For each configured sensor the file retains the existing
`temperature_<short_name>` and `temperature_<short_name>_counts` TH1D objects.
Their X axis uses the same CAEN run-start origin as
`time_from_run_start_s`. The configured bin width, mean temperature, SEM, and
sample-count behavior are unchanged.

Before atomic publication, the writer reopens and validates the candidate ROOT
file. Validation covers the TTree class, exact schema and types, removed branch
absence, interval and ordering, sensor mapping, relative/absolute time
coherence with a float64-aware tolerance, and agreement among TTree, metadata,
per-sensor counts, and `_counts` histograms.
