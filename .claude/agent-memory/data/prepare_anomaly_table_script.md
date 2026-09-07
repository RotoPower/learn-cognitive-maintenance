---
name: prepare-anomaly-table-script
description: Reusable script scripts/prepare_anomaly_table.py builds fleet anomaly input tables from data/sim/sensors.csv
metadata:
  type: project
---

`scripts/prepare_anomaly_table.py --as-of <ISO ts> --lookback-days <N>` builds
`data/derived/anomaly_fleet_<as-of date>.parquet` (long format: timestamp,
asset, tag, value, dead) plus a `.meta.json` sidecar (source, window, dead
tags, outage windows, missing-hour gaps, duplicate timestamps found).

Design choices (so the modeler/validator interpreting the parquet don't have
to guess):
- `PLANT.LOAD` is its own asset row (`asset="PLANT"`, `tag="PLANT.LOAD"`), not
  duplicated onto every other asset's rows — join on `timestamp` if needed.
- Duplicate timestamps: kept first occurrence, dropped the rest (quirk states
  duplicates are identical).
- Missing hours: left absent, never filled/imputed — downstream rolling-window
  code must tolerate an irregular hourly index.
- Dead tags (`GT1.BRG_TEMP_2`) are NOT dropped; flagged via boolean `dead`
  column and listed in the sidecar, so it's an explicit choice for whoever
  builds features, not a silent drop.
- Ground truth is never read to build this table (uses `data/sim/sensors.csv`
  only, cross-checkable via the plant API's read-only endpoints).

See [[dataset-quirks-sim]] for the exact quirk timestamps this script's output
was validated against on 2026-09-08 (as-of=2024-09-20T00:00:00,
lookback-days=81, i.e. window 2024-07-01..2024-09-20).

**How to apply:** rerun this script rather than hand-rolling a new extraction
whenever a new as-of/window anomaly table is requested; it already handles the
four documented data quirks.
