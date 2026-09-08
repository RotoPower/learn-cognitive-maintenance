# EDA — `readings_2024-07`

Source: `data/derived/readings_2024-07.parquet` (not modified). Current sim time used for leakage check: not available (no API running); used end of dataset 2024-07-31 23:00:00.

## Dataset

- Shape: 744 rows x 25 columns (24 tags + timestamp)
- Dtypes: timestamp datetime64[us]; all tags {'float64'}
- Time range: 2024-07-01 00:00:00 -> 2024-07-31 23:00:00 (expected 744 hourly stamps, found 744 unique, 0 rows share a duplicated stamp)
- Assets: BFP1, BFP2, CTF1, GT1, PLANT
- Tags: PLANT.LOAD, GT1.LOAD_MW, GT1.EXH_TEMP, GT1.CDP, GT1.FUEL_FLOW, GT1.VIB_1, GT1.BRG_TEMP_1, GT1.BRG_TEMP_2, BFP1.FLOW, BFP1.DISCH_PRESS, BFP1.VIB_DE, BFP1.VIB_NDE, BFP1.BRG_TEMP_DE, BFP1.MOTOR_CURR, BFP2.FLOW, BFP2.DISCH_PRESS, BFP2.VIB_DE, BFP2.VIB_NDE, BFP2.BRG_TEMP_DE, BFP2.MOTOR_CURR, CTF1.SPEED, CTF1.VIB, CTF1.GBX_OIL_TEMP, CTF1.MOTOR_CURR

## Per-tag profile

| tag | null % | constant | min | median | max | largest gap | where | dup ts |
|---|---|---|---|---|---|---|---|---|
| PLANT.LOAD | 0.0 | no | 0.450 | 0.658 | 0.903 | 0 days 01:00:00 | nan | 0 |
| GT1.LOAD_MW | 0.0 | no | 52.163 | 78.703 | 109.017 | 0 days 01:00:00 | nan | 0 |
| GT1.EXH_TEMP | 0.0 | no | 517.422 | 534.877 | 550.647 | 0 days 01:00:00 | nan | 0 |
| GT1.CDP | 0.0 | no | 13.860 | 15.036 | 16.327 | 0 days 01:00:00 | nan | 0 |
| GT1.FUEL_FLOW | 0.0 | no | 4.403 | 5.714 | 7.077 | 0 days 01:00:00 | nan | 0 |
| GT1.VIB_1 | 0.0 | no | 1.568 | 2.056 | 2.449 | 0 days 01:00:00 | nan | 0 |
| GT1.BRG_TEMP_1 | 0.0 | no | 74.528 | 77.440 | 79.660 | 0 days 01:00:00 | nan | 0 |
| GT1.BRG_TEMP_2 | 0.0 | YES | 81.400 | 81.400 | 81.400 | 0 days 01:00:00 | nan | 0 |
| BFP1.FLOW | 9.7 | no | 195.292 | 243.314 | 292.363 | 3 days 01:00:00 | 07-18 23:00 -> 07-22 00:00 | 0 |
| BFP1.DISCH_PRESS | 9.7 | no | 157.628 | 163.564 | 168.593 | 3 days 01:00:00 | 07-18 23:00 -> 07-22 00:00 | 0 |
| BFP1.VIB_DE | 9.7 | no | 1.340 | 1.751 | 2.053 | 3 days 01:00:00 | 07-18 23:00 -> 07-22 00:00 | 0 |
| BFP1.VIB_NDE | 9.7 | no | 1.098 | 1.458 | 1.862 | 3 days 01:00:00 | 07-18 23:00 -> 07-22 00:00 | 0 |
| BFP1.BRG_TEMP_DE | 9.7 | no | 58.995 | 61.423 | 64.337 | 3 days 01:00:00 | 07-18 23:00 -> 07-22 00:00 | 0 |
| BFP1.MOTOR_CURR | 9.7 | no | 252.052 | 294.818 | 337.045 | 3 days 01:00:00 | 07-18 23:00 -> 07-22 00:00 | 0 |
| BFP2.FLOW | 0.0 | no | 194.870 | 241.454 | 292.995 | 0 days 01:00:00 | nan | 0 |
| BFP2.DISCH_PRESS | 0.0 | no | 158.635 | 163.337 | 168.582 | 0 days 01:00:00 | nan | 0 |
| BFP2.VIB_DE | 0.0 | no | 1.377 | 1.740 | 2.146 | 0 days 01:00:00 | nan | 0 |
| BFP2.VIB_NDE | 0.0 | no | 1.047 | 1.452 | 1.805 | 0 days 01:00:00 | nan | 0 |
| BFP2.BRG_TEMP_DE | 0.0 | no | 58.528 | 61.351 | 63.996 | 0 days 01:00:00 | nan | 0 |
| BFP2.MOTOR_CURR | 0.0 | no | 250.868 | 293.327 | 339.998 | 0 days 01:00:00 | nan | 0 |
| CTF1.SPEED | 0.0 | no | 116.954 | 117.985 | 118.937 | 0 days 01:00:00 | nan | 0 |
| CTF1.VIB | 0.0 | no | 1.799 | 2.378 | 2.855 | 0 days 01:00:00 | nan | 0 |
| CTF1.GBX_OIL_TEMP | 0.0 | no | 53.950 | 57.150 | 60.343 | 0 days 01:00:00 | nan | 0 |
| CTF1.MOTOR_CURR | 0.0 | no | 85.066 | 92.811 | 99.965 | 0 days 01:00:00 | nan | 0 |

## Findings

- **Dead tag(s)**: GT1.BRG_TEMP_2 — constant for the whole month (value 81.400). Zero information; exclude from features.
- **Sensor outage on BFP1**: every BFP1 tag is null from 2024-07-19 00:00:00 to 2024-07-21 23:00:00 (72 hours). Other assets read normally, so this is a data-acquisition outage, not a plant event.
- **No missing hours**: the hourly grid is complete for the month.
- **Leakage columns**: none (no health/failure/rul columns present).
- **No future rows** relative to the reference time.
- **Load coverage**: PLANT.LOAD spans 0.450–0.903; asset tags move with it, so compare periods only after load normalisation.
- **Vibration levels normal**: all VIB tags peak below 4 mm/s this month; no visible degradation.

## Recommended fixes

- Drop dead tag(s) from any feature set; keep a note in the data sidecar (`dead_tags`).
- Treat asset-wide null blocks as outage windows: keep rows, do not impute, and let rolling windows skip them.
- De-duplicate on timestamp keeping the first row (duplicates are identical).
- Leave missing hours absent; rolling features must tolerate an irregular hourly index.
- Join `PLANT.LOAD` on timestamp and load-normalise before z-scoring or comparing months.
