# EDA — `30d` (trailing 30 days of `data/sim/sensors.csv`)

Generated 2026-09-08. Source: `data/sim/sensors.csv` (read-only, not modified).
No file is named `30d`; the window is defined as the 30 days ending at the last
timestamp in the file, which stands in for the current sim time
(no persisted `SimClock` state exists on disk).

## Dataset summary

| | Full file | 30d window |
|---|---|---|
| Rows | 8756 | 720 |
| Columns | 25 (timestamp + 24 tags) | same |
| Time range | 2024-01-01 00:00 -> 2024-12-30 23:00 | 2024-12-01 00:00 -> 2024-12-30 23:00 |
| Cadence | hourly | hourly, no gaps |
| Dtypes | 24 float64, 1 datetime64 | same |

Assets present: `GT1`, `BFP1`, `BFP2`, `CTF1`, plus `PLANT.LOAD`.
Tags present match `docs/plant.md` exactly (7 GT1, 6 each BFP1/BFP2, 4 CTF1).

## Per-tag profile (30d window)

| Tag | Null % | Constant | Min | Median | Max | Std | Max gap | Dup ts |
|---|---|---|---|---|---|---|---|---|
| PLANT.LOAD | 0.0 | no | 0.450 | 0.671 | 0.923 | 0.121 | 1h | 0 |
| GT1.LOAD_MW | 0.0 | no | 53.02 | 80.51 | 111.51 | 14.49 | 1h | 0 |
| GT1.EXH_TEMP | 0.0 | no | 518.3 | 535.0 | 554.4 | 7.46 | 1h | 0 |
| GT1.CDP | 0.0 | no | 13.70 | 15.13 | 16.36 | 0.61 | 1h | 0 |
| GT1.FUEL_FLOW | 0.0 | no | 4.44 | 5.77 | 7.14 | 0.67 | 1h | 0 |
| GT1.VIB_1 | 0.0 | no | 1.74 | 2.08 | 2.45 | 0.13 | 1h | 0 |
| GT1.BRG_TEMP_1 | 0.0 | no | 74.85 | 77.55 | 80.37 | 0.92 | 1h | 0 |
| GT1.BRG_TEMP_2 | 0.0 | **yes** | 81.4 | 81.4 | 81.4 | 0.00 | 1h | 0 |
| BFP1.FLOW | 0.0 | no | 194.2 | 244.0 | 297.1 | 24.29 | 1h | 0 |
| BFP1.DISCH_PRESS | 0.0 | no | 158.3 | 163.7 | 168.8 | 2.31 | 1h | 0 |
| BFP1.VIB_DE | 0.0 | no | 1.32 | 1.76 | 2.17 | 0.13 | 1h | 0 |
| BFP1.VIB_NDE | 0.0 | no | 1.11 | 1.45 | 1.80 | 0.11 | 1h | 0 |
| BFP1.BRG_TEMP_DE | 0.0 | no | 59.08 | 61.45 | 63.90 | 0.97 | 1h | 0 |
| BFP1.MOTOR_CURR | 0.0 | no | 252.0 | 295.8 | 344.1 | 21.77 | 1h | 0 |
| BFP2.FLOW | 0.0 | no | 195.6 | 244.5 | 297.5 | 24.30 | 1h | 0 |
| BFP2.DISCH_PRESS | 0.0 | no | 158.0 | 163.6 | 169.1 | 2.31 | 1h | 0 |
| BFP2.VIB_DE | 0.0 | no | 1.40 | 1.76 | 2.11 | 0.12 | 1h | 0 |
| BFP2.VIB_NDE | 0.0 | no | 1.09 | 1.46 | 1.84 | 0.12 | 1h | 0 |
| BFP2.BRG_TEMP_DE | 0.0 | no | 59.05 | 61.48 | 64.01 | 0.98 | 1h | 0 |
| BFP2.MOTOR_CURR | 0.0 | no | 252.2 | 296.2 | 341.0 | 21.96 | 1h | 0 |
| CTF1.SPEED | 0.0 | no | 117.0 | 118.0 | 119.0 | 0.28 | 1h | 0 |
| CTF1.VIB | 0.0 | no | 1.94 | 2.38 | 2.83 | 0.16 | 1h | 0 |
| CTF1.GBX_OIL_TEMP | 0.0 | no | 54.12 | 57.29 | 61.11 | 1.26 | 1h | 0 |
| CTF1.MOTOR_CURR | 0.0 | no | 85.64 | 93.11 | 100.50 | 3.22 | 1h | 0 |

Drift check: no tag's 30d mean deviates from its Jan-Nov mean by more than 0.5 std.
Load coupling: 13 of 23 live tags have |r| >= 0.84 with `PLANT.LOAD`; vibration tags 0.22-0.60; `CTF1.SPEED` is load-independent (r = 0.01), as documented.

## Leakage check

- No column named like `health`, `failure`, or `rul` in `sensors.csv`.
- No rows dated after the assumed sim time (2024-12-30 23:00).
- `data/sim/ground_truth.json` sits beside the sensor file and holds `failures`
  and onset dates. It was not opened for this profile beyond confirming its
  schema; it must never be joined to the feature table.
- Caveat: if a live `SimClock` is later set earlier than 2024-12-30, every row
  after that clock is future data. The static CSV carries no clock, so any
  training split must define the cutoff explicitly.

## Findings

- **Window is clean and fully regular**: 720 hourly rows, 0 nulls, 0 duplicate timestamps, no gaps, every tag in `docs/plant.md` present.
- **`GT1.BRG_TEMP_2` is a dead sensor**: constant 81.4 across the window and the full file, matching the documented transmitter failure. Zero information; drop it.
- **No degradation visible in the window**: all three scripted failures (BFP2 bearing wear to 2024-09-27, CTF1 gearbox wear to 2024-11-21, GT1 compressor fouling to 2024-11-26) were repaired before 2024-12-01. The window is post-repair healthy baseline only.
- **A model trained or scored only on this window has no positive class**: for anomaly work it is a valid baseline; for failure prediction it is useless on its own.
- **Load explains most variance**: 13 tags correlate >= 0.84 with `PLANT.LOAD`. Raw thresholds will fire on load swings; features need load-normalisation (residual vs. expected-at-load).
- **Vibration tags are the least load-driven** (r 0.22-0.60) and carry the strongest failure-mode gains, so they are the best candidates for direct rolling statistics.
- **Full-file defects sit outside the window**: one 6-hour gap on 2024-05-11 (5 missing hours), one duplicate row at 2024-03-28 14:00 (identical values), and 72 null rows for all six BFP1 tags on 2024-07-19 to 2024-07-21.
- **File ends 2024-12-30 23:00**, not year end, and 2024-01-01 to 2024-12-30 should give 8760 rows but the file has 8756 (5 missing, 1 duplicated). Row count is not a safe proxy for coverage.
- **No leakage columns present** and no future-dated rows against the assumed sim time. The ground-truth file is adjacent on disk and is the main leakage risk.
- **Sim-time is assumed, not read**: the CSV has no clock. `30d` was resolved as "last 30 days of the file"; a different cutoff changes every result above.

## Recommended fixes

1. Drop `GT1.BRG_TEMP_2` from every feature table; optionally keep a `dead_sensor` flag for the asset.
2. Do not build a predict-task dataset from the last 30 days alone. Use the full year with a time-based split whose cutoff is passed explicitly (e.g. train <= 2024-10-31, test after), so the three failure windows land on the intended side.
3. Add load-normalised residual features: fit `tag ~ PLANT.LOAD` on a healthy reference period per asset and use the residual, not the raw value, for anomaly scoring.
4. In the loader for `data/derived/`: sort by timestamp, drop exact duplicate rows (the 2024-03-28 14:00 pair), reindex to a full hourly grid, and forward-fill or mask the 2024-05-11 gap and the BFP1 outage of 2024-07-19 to 2024-07-21 with an explicit `is_imputed` column.
5. Make the "current sim time" a required argument of every EDA / feature / training entrypoint and record it in the run's JSON artefact, so the "future rows" check is reproducible.
6. Guard the pipeline against reading `data/sim/ground_truth.json` outside the validator (path allowlist or a test that asserts the feature builder never opens it).
