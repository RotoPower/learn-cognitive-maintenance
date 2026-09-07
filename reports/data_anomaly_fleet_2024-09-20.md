# Data profile: anomaly_fleet_2024-09-20

Source: `data/sim/sensors.csv` (read-only historian export).
Table: `data/derived/anomaly_fleet_2024-09-20.parquet`
Sidecar: `data/derived/anomaly_fleet_2024-09-20.meta.json`

- As-of (sim clock, paused): 2024-09-20T00:00:00
- Window extracted: 2024-07-01T00:00:00 -> 2024-09-20T00:00:00 (81 days, 1945 hourly rows per tag)
  - Scoring window for the anomaly method: 2024-09-13 -> 2024-09-20
  - Reach-back for the 30-day rolling baseline: from 2024-08-01, extended to 2024-07-01 for safety margin
- Assets: GT1, BFP1, BFP2, CTF1, plus PLANT (load factor, its own asset/tag `PLANT.LOAD`)
- 24 tags total, long format, 46,680 rows

## Per-tag profile

| tag | n | null % | constant? | min | median | max | largest gap |
|---|---|---|---|---|---|---|---|
| BFP1.BRG_TEMP_DE | 1945 | 3.70% | False | 58.728 | 61.435 | 64.337 | 3 days 01:00:00 |
| BFP1.DISCH_PRESS | 1945 | 3.70% | False | 157.628 | 163.563 | 169.151 | 3 days 01:00:00 |
| BFP1.FLOW | 1945 | 3.70% | False | 194.843 | 243.499 | 297.109 | 3 days 01:00:00 |
| BFP1.MOTOR_CURR | 1945 | 3.70% | False | 251.462 | 295.649 | 343.181 | 3 days 01:00:00 |
| BFP1.VIB_DE | 1945 | 3.70% | False | 1.340 | 1.754 | 2.070 | 3 days 01:00:00 |
| BFP1.VIB_NDE | 1945 | 3.70% | False | 1.098 | 1.456 | 1.862 | 3 days 01:00:00 |
| BFP2.BRG_TEMP_DE | 1945 | 0.00% | False | 58.528 | 61.638 | 71.134 | 0 days 01:00:00 |
| BFP2.DISCH_PRESS | 1945 | 0.00% | False | 158.104 | 163.469 | 169.397 | 0 days 01:00:00 |
| BFP2.FLOW | 1945 | 0.00% | False | 194.870 | 242.977 | 296.427 | 0 days 01:00:00 |
| BFP2.MOTOR_CURR | 1945 | 0.00% | False | 250.868 | 294.995 | 343.689 | 0 days 01:00:00 |
| BFP2.VIB_DE | 1945 | 0.00% | False | 1.331 | 1.771 | 3.054 | 0 days 01:00:00 |
| BFP2.VIB_NDE | 1945 | 0.00% | False | 1.047 | 1.465 | 1.886 | 0 days 01:00:00 |
| CTF1.GBX_OIL_TEMP | 1945 | 0.00% | False | 53.950 | 57.226 | 60.844 | 0 days 01:00:00 |
| CTF1.MOTOR_CURR | 1945 | 0.00% | False | 85.066 | 92.959 | 101.405 | 0 days 01:00:00 |
| CTF1.SPEED | 1945 | 0.00% | False | 116.954 | 117.987 | 118.964 | 0 days 01:00:00 |
| CTF1.VIB | 1945 | 0.00% | False | 1.799 | 2.380 | 3.045 | 0 days 01:00:00 |
| GT1.BRG_TEMP_1 | 1945 | 0.00% | False | 74.528 | 77.490 | 80.317 | 0 days 01:00:00 |
| GT1.BRG_TEMP_2 | 1945 | 0.00% | True | 81.400 | 81.400 | 81.400 | 0 days 01:00:00 |
| GT1.CDP | 1945 | 0.00% | False | 13.860 | 15.074 | 16.479 | 0 days 01:00:00 |
| GT1.EXH_TEMP | 1945 | 0.00% | False | 516.894 | 535.117 | 551.969 | 0 days 01:00:00 |
| GT1.FUEL_FLOW | 1945 | 0.00% | False | 4.403 | 5.741 | 7.149 | 0 days 01:00:00 |
| GT1.LOAD_MW | 1945 | 0.00% | False | 52.163 | 79.900 | 110.204 | 0 days 01:00:00 |
| GT1.VIB_1 | 1945 | 0.00% | False | 1.568 | 2.064 | 2.511 | 0 days 01:00:00 |
| PLANT.LOAD | 1945 | 0.00% | False | 0.450 | 0.666 | 0.919 | 0 days 01:00:00 |

(`n` = rows in the extracted window incl. nulls; "largest gap" = largest interval between consecutive *non-null* timestamps for that tag.)

## Quirks found

1. **BFP1 sensor outage**: all BFP1 tags NaN from 2024-07-19T00:00:00 to 2024-07-21T23:00:00 (72 hours, 3.70% null on each BFP1 tag). Falls inside the extracted window (used for baseline, not in the 09-13..09-20 scoring window). Left as NaN, not imputed; recorded in sidecar `outage_windows`.
2. **Dead tag** `GT1.BRG_TEMP_2`: constant 81.400 for the entire window (and, per docs/plant.md, the whole horizon). Not dropped — flagged via boolean `dead` column in the parquet and listed in the sidecar `dead_tags`. Modelers should exclude it from features.
3. **Duplicated timestamp** (from full-series scan): 2024-03-28T14:00:00 appears twice in the raw CSV with identical values — outside our 07-01..09-20 window but deduplicated (kept first) across the whole file before windowing, so it cannot leak in if lookback is extended.
4. **Missing-hours gap** (from full-series scan): 2024-05-11T00:00 to 2024-05-11T04:00 (5 hourly rows absent) — outside our extraction window, no impact here, but noted for anyone extending the lookback further back.
5. **BFP2 vibration/bearing-temp elevation**: BFP2.VIB_DE and BFP2.BRG_TEMP_DE show above-baseline max values (3.05 mm/s, 71.1 degC vs baseline ~1.8/62). This lines up with the documented `bearing_wear` scenario onset at day 240 (2024-08-28) in `plant/faults.yaml`, i.e. genuine degrading-asset signal within the extraction window, not a data-quality defect. This is exactly the kind of anomaly the fleet run should be sensitive to on BFP2; do not treat as noise to clean out.

## Notes for the modeler

- `PLANT.LOAD` is stored as its own asset (`asset="PLANT"`, `tag="PLANT.LOAD"`) in the long table rather than duplicated onto each asset's rows; join it back on `timestamp` when load-normalising other tags (see docs/plant.md: every asset's readings move with load).
- No row in the table has `timestamp` after 2024-09-20T00:00:00.
- Missing hours are left absent (no synthetic fill) — resampling/rolling-window code must tolerate irregular gaps, not assume a fixed-length hourly index.
- No ground-truth file was read to build this table.
