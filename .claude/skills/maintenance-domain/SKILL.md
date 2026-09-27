---
name: maintenance-domain
description: Domain reference for the simulated plant — asset ids, tag names and units, failure modes with their sensor symptoms, and the maintenance-log schema. Generated from docs/plant.md; preloaded into the data, modeler and validator agents. Use whenever interpreting a tag, choosing features for a failure mode, or reading the maintenance log.
---
# Plant maintenance domain

Source of truth: `docs/plant.md` and `plant/sim.py` (kept equal by `tests/test_docs.py`). If they disagree with this file, they win; regenerate this skill.

## Assets (14)

Tag names are `<ASSET>.<TAG>` with **no hyphen** in the asset id (`GT1.EXH_TEMP`). `plant/faults.yaml` and API callers may write `GT-1`; both forms resolve to the canonical id.

| Canonical id | Asset | Drive / note |
|---|---|---|
| `GT1`, `GT2` | Gas turbines, 120 MW class | `GT1.BRG_TEMP_2` is a dead sensor; GT2's works |
| `HRSG1`, `HRSG2` | Heat recovery steam generators (one per GT) | — |
| `ST1` | Steam turbine, 55 MW class | — |
| `GEN1` | Generator (block) | — |
| `TX1` | Main step-up transformer | — |
| `BFP1`, `BFP2` | Boiler feed pumps A, B (duty) | motor |
| `BFP3` | Boiler feed pump C (standby) | motor |
| `CWP1`, `CWP2` | Cooling water pumps | motor |
| `CTF1`, `CTF2` | Cooling tower fan cells | motor via gearbox |

`GT1`, `BFP1`, `BFP2`, `CTF1` are the original MVP plant; their readings did not change when the plant grew.

`PLANT.LOAD` is the plant load factor (0.45–1.0), daily cycle with a 16:00 peak and 04:00 trough, lower at weekends, plus slow drift. Every asset's readings move with it, so **normalise for load before comparing periods**.

## Tags (82 incl. PLANT.LOAD)

Sampling is hourly. `baseline` is at load 0.75 and full health. `load_gain` is the change per unit of `(load − 0.75)`. `noise` is 1σ white noise. Rows marked `x` apply to every asset of the family.

| Tag | Unit | Baseline | Load gain | Noise |
|---|---|---|---|---|
| `GTx.LOAD_MW` · `EXH_TEMP` · `CDP` · `FUEL_FLOW` | MW · degC · bar · kg/s | 90 · 540 · 15.5 · 6.2 | 120 · 60 · 5 · 5.5 | 0.8 · 2 · 0.08 · 0.05 |
| `GTx.VIB_1` · `BRG_TEMP_1` · `BRG_TEMP_2` | mm/s · degC · degC | 2.1 · 78 · 80 (GT1: dead at 81.4) | 0.4 · 6 · 6 | 0.12 · 0.6 · 0.6 |
| `HRSGx.STEAM_FLOW` · `FW_FLOW` · `DRUM_PRESS` · `STACK_TEMP` · `MAKEUP_FLOW` | t/h · t/h · bar · degC · t/h | 150 · 152 · 95 · 95 · 2.0 | 140 · 140 · 20 · 12 · 0.5 | 1.5 · 1.5 · 0.4 · 0.8 · 0.15 |
| `ST1.LOAD_MW` · `INLET_PRESS` · `INLET_TEMP` · `STAGE_PRESS` | MW · bar · degC · bar | 55 · 90 · 540 · 60 | 70 · 18 · 10 · 14 | 0.6 · 0.4 · 1.5 · 0.3 |
| `ST1.EXH_PRESS` · `VIB_1` · `BRG_TEMP_1` | kPa abs · mm/s · degC | 8.0 · 1.9 · 75 | 2 · 0.3 · 5 | 0.08 · 0.1 · 0.5 |
| `BFPx.FLOW` · `DISCH_PRESS` · `VIB_DE` · `VIB_NDE` · `BRG_TEMP_DE` · `MOTOR_CURR` | t/h · bar · mm/s · mm/s · degC · A | 260 · 165 · 1.8 · 1.5 · 62 · 310 | 200 · 18 · 0.6 · 0.5 · 7 · 180 | 2.5 · 0.7 · 0.1 · 0.1 · 0.5 · 2 |
| `CWPx.FLOW` · `DISCH_PRESS` · `VIB_DE` · `VIB_NDE` | m3/h · bar · mm/s · mm/s | 9000 · 2.5 · 2.0 · 1.7 | 500 · 0.2 · 0.2 · 0.2 | 40 · 0.02 · 0.1 · 0.1 |
| `CWPx.BRG_TEMP_DE` · `MOTOR_CURR` · `SEAL_LEAK_FLOW` | degC · A · l/h | 55 · 180 · 2.0 | 3 · 20 · 0.2 | 0.5 · 1.5 · 0.2 |
| `CTFx.SPEED` · `VIB` · `GBX_OIL_TEMP` · `MOTOR_CURR` | rpm · mm/s · degC · A | 118 · 2.4 · 58 · 95 | 0 · 0.3 · 9 · 25 | 0.3 · 0.15 · 0.6 · 1 |
| `GEN1.MW` · `STATOR_TEMP_1` · `STATOR_TEMP_2` · `COOLANT_TEMP` · `PD_ACTIVITY` | MW · degC · degC · degC · pC | 140 · 95 · 96 · 40 · 150 | 190 · 25 · 25 · 6 · 30 | 1 · 0.8 · 0.8 · 0.4 · 15 |
| `TX1.LOAD_MVA` · `TOP_OIL_TEMP` · `WINDING_TEMP` · `H2_PPM` · `MOISTURE_PPM` | MVA · degC · degC · ppm · ppm | 150 · 60 · 75 · 30 · 10 | 200 · 25 · 35 · 0 · 0 | 1.5 · 0.6 · 0.8 · 1.5 · 0.5 |

## Failure modes and symptoms (8)

Health `h` is hidden, 1 = as new, 0 = failed, non-increasing between repairs. A symptom is added to the sensor as `gain × (1 − h)^shape`; degradation accelerates (`h = 1 − x^2.5` over the fault window), so symptoms are faint for most of the window and steep in the last ~20 %. Sign tells direction. A mode happens only on the assets listed.

| Mode | Applies to | Symptom tags (gain, shape) | What to look for |
|---|---|---|---|
| `bearing_wear` | BFP1-3, CWP1-2 | `VIB_DE` (+4.5, 2.0) · `BRG_TEMP_DE` (+22, 1.5) · `VIB_NDE` (+0.8, 2.0) · `MOTOR_CURR` (+6, 1.0) | DE vibration and DE bearing temperature rising together; NDE barely moves |
| `compressor_fouling` | GT1, GT2 | `CDP` (−1.4, 1.0) · `EXH_TEMP` (+28, 1.0) · `FUEL_FLOW` (+0.45, 1.0) · `LOAD_MW` (−5, 1.0) | Linear drift: pressure ratio falls while exhaust temperature and fuel flow rise at the same load |
| `gearbox_wear` | CTF1-2 | `GBX_OIL_TEMP` (+20, 1.5) · `VIB` (+3.5, 2.0) · `MOTOR_CURR` (+7, 1.0) | Oil temperature leads, vibration follows late |
| `tube_leak` | HRSG1-2 | `MAKEUP_FLOW` (+6, 1.5) · `FW_FLOW` (+6, 1.5) · `STACK_TEMP` (−8, 1.5) · `DRUM_PRESS` (−2, 1.0) | Feedwater runs above steam flow and make-up water climbs; stack cools |
| `seal_leak` | CWP1-2 | `SEAL_LEAK_FLOW` (+25, 1.5) · `DISCH_PRESS` (−0.15, 1.0) · `FLOW` (−200, 1.0) | Leak flow climbs; head and flow sag slightly |
| `blade_erosion` | ST1 | `LOAD_MW` (−3, 1.0) · `STAGE_PRESS` (+2.5, 1.0) · `VIB_1` (+1.5, 2.0) | Output falls at the same steam conditions; first-stage pressure rises; vibration late |
| `winding_overheat` | GEN1 | `STATOR_TEMP_1` (+18, 1.5) · `STATOR_TEMP_2` (+14, 1.5) · `PD_ACTIVITY` (+600, 2.0) · `COOLANT_TEMP` (+2, 1.0) | Stator hot spot with normal cooling; partial discharge late |
| `oil_degradation` | TX1 | `H2_PPM` (+220, 1.5) · `MOISTURE_PPM` (+25, 1.0) · `TOP_OIL_TEMP` (+4, 1.0) | Dissolved hydrogen and moisture climb over months; temperature barely moves |

Non-symptom tags on a degrading asset stay at baseline. Assets do not influence one another. CWP1/CWP2 can fail by two modes (bearing_wear, seal_leak): tell them apart by the tags that move.

## Events and data quirks (deliberate)

- **Sensor outage**: all tags of an asset read NaN (`null` in the API) for the event window. Not a failure.
- **Dead tag**: `GT1.BRG_TEMP_2` constant for the whole horizon.
- **Missing hours**: a short block of hourly rows absent from exports.
- **Duplicated timestamp**: one row exported twice with identical values.

A profiling step must find all four before modelling.

## Maintenance log schema

`GET /maintenance/log?asset_id=` returns a list sorted by `timestamp`, entries of two kinds:

```json
{ "kind": "corrective_repair", "asset_id": "BFP2",
  "timestamp": "2024-09-27T00:00:00",
  "description": "Failure: bearing wear; component replaced" }

{ "kind": "workorder", "id": "WO-00001", "asset_id": "BFP2",
  "type": "inspection | repair | replacement | lubrication | other",
  "description": "vib check",
  "timestamp": "2024-09-01T00:00:00",
  "scheduled_for": "2024-09-03T08:00:00",
  "status": "open" }
```

- `corrective_repair` entries and work orders appear only once they are in the past relative to sim time. After a repair, the asset's health is 1 again: **reset any degradation features at that timestamp**.
- `timestamp` on a work order is the sim time it was raised. Work orders are created with `POST /maintenance/workorder {"asset_id","type","description","scheduled_for"}` (READ token).
- The log is the only legitimate label source for the modeler. Ground truth (`/admin/ground_truth`, hidden health, `failures()`, the scenario scripts `plant/faults*.yaml`) is for the validator only — never a model input.

## Time and access

- Sim horizon: 2024-01-01 to 2025-12-31, hourly. Current sim time comes from `GET /clock`; history requests are clamped to it, so nothing after "now" is observable.
- READ token: clock, assets, tags, maintenance, alerts, predictions, playbook. ADMIN token: ground truth, fault injection, reset, model artifacts, playbook load.
