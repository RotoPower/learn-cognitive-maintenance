# EDA — `readings_2024-08`

Source: `data/derived/readings_2024-08.parquet` (not modified). Current sim time used for leakage check: not available (no API running); used end of dataset 2024-08-31 23:00:00.

## Dataset

- Shape: 744 rows x 25 columns (24 tags + timestamp)
- Dtypes: timestamp datetime64[us]; all tags {'float64'}
- Time range: 2024-08-01 00:00:00 -> 2024-08-31 23:00:00 (expected 744 hourly stamps, found 744 unique, 0 rows share a duplicated stamp)
- Assets: BFP1, BFP2, CTF1, GT1, PLANT
- Tags: PLANT.LOAD, GT1.LOAD_MW, GT1.EXH_TEMP, GT1.CDP, GT1.FUEL_FLOW, GT1.VIB_1, GT1.BRG_TEMP_1, GT1.BRG_TEMP_2, BFP1.FLOW, BFP1.DISCH_PRESS, BFP1.VIB_DE, BFP1.VIB_NDE, BFP1.BRG_TEMP_DE, BFP1.MOTOR_CURR, BFP2.FLOW, BFP2.DISCH_PRESS, BFP2.VIB_DE, BFP2.VIB_NDE, BFP2.BRG_TEMP_DE, BFP2.MOTOR_CURR, CTF1.SPEED, CTF1.VIB, CTF1.GBX_OIL_TEMP, CTF1.MOTOR_CURR

## Per-tag profile

| tag | null % | constant | min | median | max | largest gap | where | dup ts |
|---|---|---|---|---|---|---|---|---|
| PLANT.LOAD | 0.0 | no | 0.450 | 0.666 | 0.894 | 0 days 01:00:00 | - | 0 |
| GT1.LOAD_MW | 0.0 | no | 52.248 | 79.633 | 108.298 | 0 days 01:00:00 | - | 0 |
| GT1.EXH_TEMP | 0.0 | no | 516.894 | 534.943 | 551.969 | 0 days 01:00:00 | - | 0 |
| GT1.CDP | 0.0 | no | 13.935 | 15.066 | 16.382 | 0 days 01:00:00 | - | 0 |
| GT1.FUEL_FLOW | 0.0 | no | 4.481 | 5.719 | 6.991 | 0 days 01:00:00 | - | 0 |
| GT1.VIB_1 | 0.0 | no | 1.635 | 2.068 | 2.511 | 0 days 01:00:00 | - | 0 |
| GT1.BRG_TEMP_1 | 0.0 | no | 75.030 | 77.480 | 80.317 | 0 days 01:00:00 | - | 0 |
| GT1.BRG_TEMP_2 | 0.0 | YES | 81.400 | 81.400 | 81.400 | 0 days 01:00:00 | - | 0 |
| BFP1.FLOW | 0.0 | no | 197.043 | 242.637 | 293.493 | 0 days 01:00:00 | - | 0 |
| BFP1.DISCH_PRESS | 0.0 | no | 158.119 | 163.508 | 168.880 | 0 days 01:00:00 | - | 0 |
| BFP1.VIB_DE | 0.0 | no | 1.397 | 1.754 | 2.070 | 0 days 01:00:00 | - | 0 |
| BFP1.VIB_NDE | 0.0 | no | 1.099 | 1.448 | 1.829 | 0 days 01:00:00 | - | 0 |
| BFP1.BRG_TEMP_DE | 0.0 | no | 58.728 | 61.373 | 63.784 | 0 days 01:00:00 | - | 0 |
| BFP1.MOTOR_CURR | 0.0 | no | 253.085 | 294.536 | 336.111 | 0 days 01:00:00 | - | 0 |
| BFP2.FLOW | 0.0 | no | 195.693 | 243.452 | 289.038 | 0 days 01:00:00 | - | 0 |
| BFP2.DISCH_PRESS | 0.0 | no | 158.104 | 163.418 | 169.153 | 0 days 01:00:00 | - | 0 |
| BFP2.VIB_DE | 0.0 | no | 1.331 | 1.752 | 2.090 | 0 days 01:00:00 | - | 0 |
| BFP2.VIB_NDE | 0.0 | no | 1.137 | 1.457 | 1.789 | 0 days 01:00:00 | - | 0 |
| BFP2.BRG_TEMP_DE | 0.0 | no | 59.120 | 61.423 | 63.909 | 0 days 01:00:00 | - | 0 |
| BFP2.MOTOR_CURR | 0.0 | no | 252.259 | 294.883 | 337.660 | 0 days 01:00:00 | - | 0 |
| CTF1.SPEED | 0.0 | no | 117.074 | 118.001 | 118.898 | 0 days 01:00:00 | - | 0 |
| CTF1.VIB | 0.0 | no | 1.800 | 2.375 | 3.045 | 0 days 01:00:00 | - | 0 |
| CTF1.GBX_OIL_TEMP | 0.0 | no | 53.988 | 57.228 | 60.844 | 0 days 01:00:00 | - | 0 |
| CTF1.MOTOR_CURR | 0.0 | no | 86.440 | 93.024 | 101.405 | 0 days 01:00:00 | - | 0 |

## Findings

- **Dead tag(s)**: GT1.BRG_TEMP_2 — constant for the whole month (value 81.400). Zero information; exclude from features.
- **No missing hours**: the hourly grid is complete for the month.
- **Leakage columns**: none (no health/failure/rul columns present).
- **No future rows** relative to the reference time.
- **Load coverage**: PLANT.LOAD spans 0.450–0.894; asset tags move with it, so compare periods only after load normalisation.
- **Vibration levels normal**: all VIB tags peak below 4 mm/s this month; no visible degradation.

## Recommended fixes

- Drop dead tag(s) from any feature set; keep a note in the data sidecar (`dead_tags`).
- Treat asset-wide null blocks as outage windows: keep rows, do not impute, and let rolling windows skip them.
- De-duplicate on timestamp keeping the first row (duplicates are identical).
- Leave missing hours absent; rolling features must tolerate an irregular hourly index.
- Join `PLANT.LOAD` on timestamp and load-normalise before z-scoring or comparing months.
