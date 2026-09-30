# Design: the full plant (roadmap Phase 1)

Status: **proposal for review**. Nothing here is implemented yet. Numbers are chosen to be
plausible for a ~150 MW combined-cycle block, not taken from a real plant.

## 1. Rules that keep the MVP valid

- **The 4 original assets do not change.** Readings are `baseline + load term + symptom + noise`,
  with noise hashed from `(seed, asset, tag, hour)`. Adding assets or tags changes no existing
  value, so the validated predict model, the scoring fixtures, the replay log and the golden set
  stay valid for 2024.
- **The 2024 script is kept.** BFP2, GT1, CTF1 failures and the BFP1 outage stay on the same days;
  new failures are added around them and in 2025.
- **One formula.** New failure modes use the same `gain * (1 - health)^shape` symptoms and the
  same accelerating health curve, so the TypeScript port only needs the new tables (parity fixture
  regenerated and extended).

## 2. Assets and tags (14 assets, 82 tags incl. PLANT.LOAD)

Existing, unchanged: **GT1** (7 tags, `BRG_TEMP_2` dead), **BFP1**, **BFP2** (6 each), **CTF1** (4).

New assets. Baseline at plant load 0.75 and full health; `load gain` per unit of `(load - 0.75)`;
`noise` is 1 sigma.

| asset | description | tag | unit | baseline | load gain | noise |
|---|---|---|---|---|---|---|
| **GT2** | Gas turbine, 120 MW class | LOAD_MW / EXH_TEMP / CDP / FUEL_FLOW / VIB_1 / BRG_TEMP_1 | as GT1 | as GT1 | as GT1 | as GT1 |
| | | BRG_TEMP_2 (working on GT2) | degC | 80.0 | 6 | 0.6 |
| **HRSG1, HRSG2** | Heat recovery steam generator | STEAM_FLOW | t/h | 150 | 140 | 1.5 |
| | | FW_FLOW (feedwater) | t/h | 152 | 140 | 1.5 |
| | | DRUM_PRESS | bar | 95 | 20 | 0.4 |
| | | STACK_TEMP | degC | 95 | 12 | 0.8 |
| | | MAKEUP_FLOW | t/h | 2.0 | 0.5 | 0.15 |
| **ST1** | Steam turbine, 55 MW class | LOAD_MW | MW | 55 | 70 | 0.6 |
| | | INLET_PRESS | bar | 90 | 18 | 0.4 |
| | | INLET_TEMP | degC | 540 | 10 | 1.5 |
| | | STAGE_PRESS (first stage) | bar | 60 | 14 | 0.3 |
| | | EXH_PRESS (condenser) | kPa abs | 8.0 | 2.0 | 0.08 |
| | | VIB_1 | mm/s | 1.9 | 0.3 | 0.10 |
| | | BRG_TEMP_1 | degC | 75 | 5 | 0.5 |
| **BFP3** | Boiler feed pump C (standby) | as BFP1/BFP2 | | | | |
| **CWP1, CWP2** | Cooling water pump | FLOW | m3/h | 9000 | 500 | 40 |
| | | DISCH_PRESS | bar | 2.5 | 0.2 | 0.02 |
| | | VIB_DE | mm/s | 2.0 | 0.2 | 0.10 |
| | | VIB_NDE | mm/s | 1.7 | 0.2 | 0.10 |
| | | BRG_TEMP_DE | degC | 55 | 3 | 0.5 |
| | | MOTOR_CURR | A | 180 | 20 | 1.5 |
| | | SEAL_LEAK_FLOW | l/h | 2.0 | 0.2 | 0.2 |
| **CTF2** | Cooling tower fan cell 2 | as CTF1 | | | | |
| **GEN1** | Generator (block) | MW | MW | 140 | 190 | 1.0 |
| | | STATOR_TEMP_1 | degC | 95 | 25 | 0.8 |
| | | STATOR_TEMP_2 | degC | 96 | 25 | 0.8 |
| | | COOLANT_TEMP | degC | 40 | 6 | 0.4 |
| | | PD_ACTIVITY (partial discharge) | pC | 150 | 30 | 15 |
| **TX1** | Main step-up transformer | LOAD_MVA | MVA | 150 | 200 | 1.5 |
| | | TOP_OIL_TEMP | degC | 60 | 25 | 0.6 |
| | | WINDING_TEMP | degC | 75 | 35 | 0.8 |
| | | H2_PPM (dissolved hydrogen) | ppm | 30 | 0 | 1.5 |
| | | MOISTURE_PPM | ppm | 10 | 0 | 0.5 |

## 3. Failure modes (8)

`gain, shape` per symptom tag; the symptom is `gain * (1 - health)^shape`. Existing three unchanged.

| mode | applies to | symptom tags (gain, shape) | typical duration | what to look for |
|---|---|---|---|---|
| bearing_wear | BFP1-3, CWP1-2 | VIB_DE (+4.5, 2) BRG_TEMP_DE (+22, 1.5) VIB_NDE (+0.8, 2) MOTOR_CURR (+6, 1) | 25-35 d | DE vibration and temperature rise together |
| compressor_fouling | GT1, GT2 | CDP (-1.4, 1) EXH_TEMP (+28, 1) FUEL_FLOW (+0.45, 1) LOAD_MW (-5, 1) | 50-65 d | slow linear drift at the same load |
| gearbox_wear | CTF1, CTF2 | GBX_OIL_TEMP (+20, 1.5) VIB (+3.5, 2) MOTOR_CURR (+7, 1) | 20-30 d | oil temperature leads, vibration late |
| **tube_leak** | HRSG1-2 | MAKEUP_FLOW (+6, 1.5) FW_FLOW (+6, 1.5) STACK_TEMP (-8, 1.5) DRUM_PRESS (-2, 1) | 15-25 d | feedwater exceeds steam flow; make-up water rises; stack cools |
| **seal_leak** | CWP1-2 | SEAL_LEAK_FLOW (+25, 1.5) DISCH_PRESS (-0.15, 1) FLOW (-200, 1) | 20-30 d | leak flow climbs; head and flow sag slightly |
| **blade_erosion** | ST1 | LOAD_MW (-3, 1) STAGE_PRESS (+2.5, 1) VIB_1 (+1.5, 2) | 60-120 d | output falls at the same steam conditions; vibration late |
| **winding_overheat** | GEN1 | STATOR_TEMP_1 (+18, 1.5) STATOR_TEMP_2 (+14, 1.5) PD_ACTIVITY (+600, 2) COOLANT_TEMP (+2, 1) | 25-40 d | stator hot spot at normal cooling; partial discharge late |
| **oil_degradation** | TX1 | H2_PPM (+220, 1.5) MOISTURE_PPM (+25, 1) TOP_OIL_TEMP (+4, 1) | 90-150 d | dissolved gas and moisture climb; temperature barely moves |

Validation of a scenario rejects a mode on an asset outside its "applies to" list.

## 4. Two-year script (`plant/faults.yaml`, 2024-01-01 .. 2025-12-31, `horizon_days: 731`)

| asset | mode | onset day | onset | duration d | failure | |
|---|---|---|---|---|---|---|
| TX1 | oil_degradation | 40 | 2024-02-10 | 130 | 2024-06-19 | new |
| CWP1 | seal_leak | 95 | 2024-04-05 | 25 | 2024-04-30 | new |
| HRSG1 | tube_leak | 180 | 2024-06-29 | 20 | 2024-07-19 | new |
| BFP2 | bearing_wear | 240 | 2024-08-28 | 30 | 2024-09-27 | existing |
| GT1 | compressor_fouling | 270 | 2024-09-27 | 60 | 2024-11-26 | existing |
| CTF1 | gearbox_wear | 300 | 2024-10-27 | 25 | 2024-11-21 | existing |
| GT2 | compressor_fouling | 380 | 2025-01-15 | 55 | 2025-03-11 | new |
| ST1 | blade_erosion | 410 | 2025-02-14 | 100 | 2025-05-25 | new |
| GEN1 | winding_overheat | 470 | 2025-04-15 | 30 | 2025-05-15 | new |
| BFP3 | bearing_wear | 520 | 2025-06-04 | 30 | 2025-07-04 | new |
| CWP2 | seal_leak | 560 | 2025-07-14 | 25 | 2025-08-08 | new |
| HRSG2 | tube_leak | 600 | 2025-08-23 | 18 | 2025-09-10 | new |
| CTF2 | gearbox_wear | 630 | 2025-09-22 | 25 | 2025-10-17 | new |
| GT1 | compressor_fouling | 640 | 2025-10-02 | 60 | 2025-12-01 | new |
| CWP1 | bearing_wear | 660 | 2025-10-22 | 35 | 2025-11-26 | new |

Events: BFP1 sensor outage days 200-203 (existing), HRSG2 sensor outage days 350-352 (new).
Quirks: dead `GT1.BRG_TEMP_2` (existing); the missing-hours block and duplicated row stay.

15 failures over two years; every mode fails at least once, tube_leak and seal_leak twice.
2024 keeps the demo story (BFP2 in September); 2025 gives the new modes a second act.

## 5. D1 free-tier budget (the constraint that decides the clock speed)

82 tags, one row per tag per sim hour. D1 counts every index write as a row written.

| option | rows written per sim hour | clock speed 60 (1,440 sim hours = 60 sim days per real day) | speed 30 (720 sim hours = 30 sim days per real day) |
|---|---|---|---|
| today (PK + `ix_readings_ts`) | 164 | 236k/day, **over 100k** | 118k/day, **over** |
| **drop `ix_readings_ts`** (PK only) | 82 | 118k/day, **over** | **59k/day (59%)** |

Proposal: **drop the index and run staging at speed 30.** A full two-year run of the clock then takes
~24 real days; demos jump the clock anyway (+7 days = 168 sim hours x 82 = 13.8k rows, fine).

The index serves only ingest's `ts = ?` / `MAX(ts)` lookups and the open `/health`; those move to the
primary key through a sentinel tag (`WHERE tag = <sentinel> AND ts ...`), which is always written.
The sentinel is `TX1.MOISTURE_PPM`, written last in each pass (revised 2026-09-29, was `PLANT.LOAD`):
a full-plant tag, so hours stored by the 4-asset plant have no sentinel and are rewritten with all
82 tags instead of being skipped.

Scoring reads per pass: 82 tags x lookback. With the GO predict model (7-day slopes) and the anomaly
baseline (30 days + 20-day minimum + 7-day window), **37 days** of lookback is enough:
82 x 37 x 24 = ~73k rows per pass. The hourly cron scores at most once per sim day, 24 passes per
real day at speed 30 = **~1.75M/day (35% of 5M)**, plus capped manual runs from the dashboard
(30/day, up to ~2.2M more): tight but inside the limit. If it gets close, score every second cron.

**Revision 2026-09-29 (local demo rehearsal).** The table above never added demo jumps to the
clock's own writes. The scripted demo's five +7-day jumps wrote 68,880 rows the first time, so
speed 30 (59k/day) plus one first-time demo is ~128k, over 100k. Weeks already stored cost nothing
(a repeat demo over them wrote 0 rows). Changes:

- **Staging clock paused** (`speed 0`) between demos; the clock only moves by demo jumps. Cron
  passes then read a row or two and write nothing. The two-year horizon fills as demos reach it.
- **Dashboard caps jumps into unstored weeks** at `JUMP_NEW_GLOBAL_DAY = 6` per day (~83k rows);
  jumps over stored weeks and Reset are not capped. Per viewer: `DEMO_PER_VIEWER_HOUR = 10`
  (the demo uses 7: five jumps and two scoring runs).

## 6. What each piece needs (Phase 1 work list)

1. `plant/sim.py`: new `TAGS`, `FAULT_MODES`, `MODE_ASSETS` (validation), 731-day horizon; tests for
   every new mode's direction and for "original assets unchanged" (hash parity on 2024 values).
2. `apps/plant-api/src/sim.ts` + `faults.ts`: same tables; parity fixture regenerated with the new
   assets (`scripts/export_parity_fixture.py`).
3. `plant/faults.yaml`: the two-year script above; `docs/plant.md` and the `maintenance-domain` skill
   regenerated from it.
4. `docs/playbook/`: five new files (tube_leak, seal_leak, blade_erosion, winding_overheat,
   oil_degradation); loader and assistant accept 8 modes.
5. Migration 0005: drop `ix_readings_ts`; ingest and `/health` queries moved to the primary key;
   staging clock paused between demos (was speed 30; section 5 revision); scoring `LOOKBACK_DAYS` 37.
6. Anomaly: symptom map (`FAILURE_MODES` in `models/anomaly.py` and `score.ts`) for the new modes;
   scoring's `target_assets` from the asset list instead of a constant.
7. Dashboard: 14 asset cards (grouped: gas turbines, HRSG and steam, pumps, cooling, electrical),
   drawer trends for up to 7 tags, demo "Inject fault" offering the modes valid for each asset.
8. Assistant: asset list, `ASSET_MODE` becomes a list per asset (CWP1 has two modes), `TAG_INFO`
   units and baselines for the new tags.
9. Not in Phase 1: predict and RUL for the new modes (Phase 3). Until then the risk score covers the
   original symptom tags; the anomaly detector covers every asset from day one.

## 7. Decisions for review

1. Asset and tag table (section 2) and failure-mode signatures (section 3).
2. The two-year script (section 4).
3. Budget: drop `ix_readings_ts`, staging clock paused between demos with a daily cap on jumps into new weeks, scoring lookback 37 days (section 5).
