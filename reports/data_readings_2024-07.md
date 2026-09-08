# EDA — `readings_2024-07`

Source: `data/raw/readings_2024-07.parquet` (read-only, not modified). Sim clock at profiling time (`GET /clock` via `plantctl`): `2024-09-01T03:24:04`, so the whole file (through 2024-07-31 23:00) is safely in the past relative to sim "now" — no leakage risk from future rows. `GET /maintenance/log` returned an empty list for all four assets (GT1, BFP1, BFP2, CTF1) as of profiling time — no corrective repairs or work orders logged yet.

## Dataset

- Shape: 744 rows x 25 columns (24 tags + timestamp)
- Dtypes: `timestamp` datetime64[us]; all 24 tag columns float64
- Time range: 2024-07-01 00:00:00 -> 2024-07-31 23:00:00 (744 expected hourly stamps, 744 unique found — complete grid)
- Assets: `BFP1`, `BFP2`, `CTF1`, `GT1`, `PLANT`
- Tags (24): `PLANT.LOAD`, `GT1.LOAD_MW`, `GT1.EXH_TEMP`, `GT1.CDP`, `GT1.FUEL_FLOW`, `GT1.VIB_1`, `GT1.BRG_TEMP_1`, `GT1.BRG_TEMP_2`, `BFP1.FLOW`, `BFP1.DISCH_PRESS`, `BFP1.VIB_DE`, `BFP1.VIB_NDE`, `BFP1.BRG_TEMP_DE`, `BFP1.MOTOR_CURR`, `BFP2.FLOW`, `BFP2.DISCH_PRESS`, `BFP2.VIB_DE`, `BFP2.VIB_NDE`, `BFP2.BRG_TEMP_DE`, `BFP2.MOTOR_CURR`, `CTF1.SPEED`, `CTF1.VIB`, `CTF1.GBX_OIL_TEMP`, `CTF1.MOTOR_CURR`

## Per-tag profile

| tag | null % | constant | min | median | max | largest gap | dup ts |
|---|---|---|---|---|---|---|---|
| PLANT.LOAD | 0.0 | no | 0.450 | 0.658 | 0.903 | none | 0 |
| GT1.LOAD_MW | 0.0 | no | 52.163 | 78.703 | 109.017 | none | 0 |
| GT1.EXH_TEMP | 0.0 | no | 517.422 | 534.877 | 550.647 | none | 0 |
| GT1.CDP | 0.0 | no | 13.860 | 15.036 | 16.327 | none | 0 |
| GT1.FUEL_FLOW | 0.0 | no | 4.403 | 5.714 | 7.077 | none | 0 |
| GT1.VIB_1 | 0.0 | no | 1.568 | 2.056 | 2.449 | none | 0 |
| GT1.BRG_TEMP_1 | 0.0 | no | 74.528 | 77.440 | 79.660 | none | 0 |
| GT1.BRG_TEMP_2 | 0.0 | **YES** | 81.400 | 81.400 | 81.400 | none | 0 |
| BFP1.FLOW | 9.68 | no | 195.292 | 243.314 | 292.363 | 72h (outage) | 0 |
| BFP1.DISCH_PRESS | 9.68 | no | 157.628 | 163.564 | 168.593 | 72h (outage) | 0 |
| BFP1.VIB_DE | 9.68 | no | 1.340 | 1.751 | 2.053 | 72h (outage) | 0 |
| BFP1.VIB_NDE | 9.68 | no | 1.098 | 1.458 | 1.862 | 72h (outage) | 0 |
| BFP1.BRG_TEMP_DE | 9.68 | no | 58.995 | 61.423 | 64.337 | 72h (outage) | 0 |
| BFP1.MOTOR_CURR | 9.68 | no | 252.052 | 294.818 | 337.045 | 72h (outage) | 0 |
| BFP2.FLOW | 0.0 | no | 194.870 | 241.454 | 292.995 | none | 0 |
| BFP2.DISCH_PRESS | 0.0 | no | 158.635 | 163.337 | 168.582 | none | 0 |
| BFP2.VIB_DE | 0.0 | no | 1.377 | 1.740 | 2.146 | none | 0 |
| BFP2.VIB_NDE | 0.0 | no | 1.047 | 1.452 | 1.805 | none | 0 |
| BFP2.BRG_TEMP_DE | 0.0 | no | 58.528 | 61.351 | 63.996 | none | 0 |
| BFP2.MOTOR_CURR | 0.0 | no | 250.868 | 293.327 | 339.998 | none | 0 |
| CTF1.SPEED | 0.0 | no | 116.954 | 117.985 | 118.937 | none | 0 |
| CTF1.VIB | 0.0 | no | 1.799 | 2.378 | 2.855 | none | 0 |
| CTF1.GBX_OIL_TEMP | 0.0 | no | 53.950 | 57.150 | 60.343 | none | 0 |
| CTF1.MOTOR_CURR | 0.0 | no | 85.066 | 92.811 | 99.965 | none | 0 |

## Findings

- **Dead tag**: `GT1.BRG_TEMP_2` constant at 81.400 for the entire month — exclude from features (documented plant quirk, matches `docs/plant.md`/skill).
- **Sensor outage on BFP1**: every BFP1 tag is null 2024-07-19T00:00:00 -> 2024-07-21T23:00:00 (72 consecutive hours, 9.68% null on those 6 columns). Other assets read normally in the same window, so this is a data-acquisition gap, not a plant health event — do not impute, keep as NaN.
- **No missing hours**: 744/744 expected hourly stamps present (complete grid outside the BFP1 outage).
- **No duplicate timestamps**: 0 rows share a timestamp.
- **No leakage columns**: no health/failure/RUL/ground-truth columns in the wide table; entire file (through 2024-07-31 23:00) is well before sim clock `2024-09-01T03:24:04` — safe to use in full.
- **No maintenance log entries**: `/maintenance/log` empty for GT1, BFP1, BFP2, CTF1 as of profiling time — no repairs/work orders to use as labels or reset points yet, so no degradation-reset logic needed for July.
- **Load coverage**: `PLANT.LOAD` spans 0.450–0.903 (mostly below the 0.75 baseline); asset tags move with load, so normalise for load before comparing across periods.
- **Vibration/temperature levels look nominal**: all VIB tags stay well under fault-symptom magnitudes (e.g. BFP `VIB_DE` max ~2.15 mm/s vs. a bearing-wear symptom gain of +4.5 mm/s); no visible onset of `bearing_wear`, `compressor_fouling`, or `gearbox_wear` within July — consistent with the earliest known fault onset (BFP2 bearing_wear, sim-day 240 = 2024-08-28) falling after this month.
- July is the one monthly file (of the two profiled so far) that contains the BFP1 sensor-outage quirk; August (`readings_2024-08.parquet`) was clean with none of the four deliberate quirks except the dead tag.

## Recommended fixes

- Drop `GT1.BRG_TEMP_2` from any feature set (or flag explicitly via a `dead_tags` sidecar entry) rather than silently including it.
- Treat the BFP1 2024-07-19->21 null block as an outage window: keep the rows, do not impute, and let rolling-window features skip/NaN-propagate across it.
- Join `PLANT.LOAD` on `timestamp` and load-normalise (subtract `load_gain × (load − 0.75)`) before comparing tag levels across months or assets.
- No de-duplication or missing-hour handling needed for July specifically, but derived pipelines should still tolerate irregular/duplicate timestamps generically since other months contain them.
