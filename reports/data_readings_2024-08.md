# EDA — `readings_2024-08`

Source: `data/raw/readings_2024-08.parquet` (read-only, not modified). Sim clock at profiling time (`GET /clock` via `plantctl`): `2024-09-01T02:00:56`, so the whole file (through 2024-08-31 23:00) is safely in the past relative to sim "now" — no leakage risk from future rows.

## Dataset

- Shape: 744 rows x 25 columns (24 tags + timestamp)
- Dtypes: `timestamp` datetime64[us]; all 24 tag columns float64
- Time range: 2024-08-01 00:00:00 -> 2024-08-31 23:00:00 (744 expected hourly stamps, 744 unique found — complete grid)
- Assets: `BFP1`, `BFP2`, `CTF1`, `GT1`, `PLANT`
- Tags (24): `PLANT.LOAD`, `GT1.LOAD_MW`, `GT1.EXH_TEMP`, `GT1.CDP`, `GT1.FUEL_FLOW`, `GT1.VIB_1`, `GT1.BRG_TEMP_1`, `GT1.BRG_TEMP_2`, `BFP1.FLOW`, `BFP1.DISCH_PRESS`, `BFP1.VIB_DE`, `BFP1.VIB_NDE`, `BFP1.BRG_TEMP_DE`, `BFP1.MOTOR_CURR`, `BFP2.FLOW`, `BFP2.DISCH_PRESS`, `BFP2.VIB_DE`, `BFP2.VIB_NDE`, `BFP2.BRG_TEMP_DE`, `BFP2.MOTOR_CURR`, `CTF1.SPEED`, `CTF1.VIB`, `CTF1.GBX_OIL_TEMP`, `CTF1.MOTOR_CURR`

## Per-tag profile

| tag | null % | constant | min | median | max | largest gap | dup ts |
|---|---|---|---|---|---|---|---|
| PLANT.LOAD | 0.0 | no | 0.450 | 0.666 | 0.894 | none | 0 |
| GT1.LOAD_MW | 0.0 | no | 52.248 | 79.633 | 108.298 | none | 0 |
| GT1.EXH_TEMP | 0.0 | no | 516.894 | 534.943 | 551.969 | none | 0 |
| GT1.CDP | 0.0 | no | 13.935 | 15.066 | 16.382 | none | 0 |
| GT1.FUEL_FLOW | 0.0 | no | 4.481 | 5.719 | 6.991 | none | 0 |
| GT1.VIB_1 | 0.0 | no | 1.635 | 2.068 | 2.511 | none | 0 |
| GT1.BRG_TEMP_1 | 0.0 | no | 75.030 | 77.480 | 80.317 | none | 0 |
| GT1.BRG_TEMP_2 | 0.0 | **YES** | 81.400 | 81.400 | 81.400 | none | 0 |
| BFP1.FLOW | 0.0 | no | 197.043 | 242.637 | 293.493 | none | 0 |
| BFP1.DISCH_PRESS | 0.0 | no | 158.119 | 163.508 | 168.880 | none | 0 |
| BFP1.VIB_DE | 0.0 | no | 1.397 | 1.754 | 2.070 | none | 0 |
| BFP1.VIB_NDE | 0.0 | no | 1.099 | 1.448 | 1.829 | none | 0 |
| BFP1.BRG_TEMP_DE | 0.0 | no | 58.728 | 61.373 | 63.784 | none | 0 |
| BFP1.MOTOR_CURR | 0.0 | no | 253.085 | 294.536 | 336.111 | none | 0 |
| BFP2.FLOW | 0.0 | no | 195.693 | 243.452 | 289.038 | none | 0 |
| BFP2.DISCH_PRESS | 0.0 | no | 158.104 | 163.418 | 169.153 | none | 0 |
| BFP2.VIB_DE | 0.0 | no | 1.331 | 1.752 | 2.090 | none | 0 |
| BFP2.VIB_NDE | 0.0 | no | 1.137 | 1.457 | 1.789 | none | 0 |
| BFP2.BRG_TEMP_DE | 0.0 | no | 59.120 | 61.423 | 63.909 | none | 0 |
| BFP2.MOTOR_CURR | 0.0 | no | 252.259 | 294.883 | 337.660 | none | 0 |
| CTF1.SPEED | 0.0 | no | 117.074 | 118.001 | 118.898 | none | 0 |
| CTF1.VIB | 0.0 | no | 1.800 | 2.375 | 3.045 | none | 0 |
| CTF1.GBX_OIL_TEMP | 0.0 | no | 53.988 | 57.228 | 60.844 | none | 0 |
| CTF1.MOTOR_CURR | 0.0 | no | 86.440 | 93.024 | 101.405 | none | 0 |

## Findings

- **Dead tag**: `GT1.BRG_TEMP_2` constant at 81.400 for the entire month — zero information, exclude from features (documented plant quirk, consistent with `docs/plant.md`).
- **No sensor-outage block, no missing hours, no duplicate timestamps this month**: the August export is a fully clean, complete hourly grid (744/744, 0% null, 0 dups). The other three deliberate data quirks (BFP1 outage, missing-hour block, duplicated stamp) documented for this dataset fall outside August — worth re-checking against neighbouring months rather than assuming they recur here.
- **No leakage columns**: no health/failure/RUL/ground-truth columns present in the wide table.
- **No future rows**: file ends 2024-08-31 23:00, well before sim clock (`2024-09-01T02:00:56` at profiling time) — safe to use in full.
- **Load coverage**: `PLANT.LOAD` spans 0.450–0.894 (below the 0.75 baseline used for tag gains most of the month); asset tags move with load, so normalise for load before comparing across periods.
- **Vibration/temperature levels look nominal**: all VIB tags stay well under fault-symptom magnitudes (e.g. BFP `VIB_DE` max 2.09 mm/s vs. a bearing-wear symptom gain of +4.5 mm/s); no visible onset of `bearing_wear`, `compressor_fouling`, or `gearbox_wear` within August per the skill's documented fault windows (earliest logged onset is BFP2 bearing_wear, sim-day 240 = 2024-08-28, but memory-recorded window was validated for a different snapshot — re-verify onset date via `/maintenance/log?asset_id=BFP2` before relying on it).
- This profile matches an earlier EDA of `data/derived/readings_2024-08.parquet` value-for-value, suggesting the derived copy was a straight pass-through of this raw file for August.

## Recommended fixes

- Drop `GT1.BRG_TEMP_2` from any feature set (or flag it explicitly, e.g. a `dead_tags` sidecar entry) — do not silently include it as a real feature.
- Join `PLANT.LOAD` on `timestamp` and load-normalise (subtract `load_gain × (load − 0.75)`) before comparing tag levels across months.
- No de-duplication or missing-hour handling needed for this specific month's file, but derived pipelines should still tolerate irregular/duplicate timestamps generically since other months are known to contain them.
