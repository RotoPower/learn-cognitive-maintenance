---
name: maintenance-domain
description: Domain reference for the simulated plant — asset ids, tag names and units, failure modes with their sensor symptoms, and the maintenance-log schema. Generated from docs/plant.md; preloaded into the data, modeler and validator agents. Use whenever interpreting a tag, choosing features for a failure mode, or reading the maintenance log.
---
# Plant maintenance domain

Source of truth: `docs/plant.md` and `plant/sim.py`. If they disagree with this file, they win; regenerate this skill.

## Assets

Tag names are `<ASSET>.<TAG>` with **no hyphen** in the asset id (`GT1.EXH_TEMP`). `plant/faults.yaml` and API callers may write `GT-1`; both forms resolve to the canonical id.

| Canonical id | Alias | Asset | Drive |
|---|---|---|---|
| `GT1`  | `GT-1`  | Gas turbine, 120 MW class | — |
| `BFP1` | `BFP-1` | Boiler feed pump A (duty) | motor |
| `BFP2` | `BFP-2` | Boiler feed pump B (duty) | motor |
| `CTF1` | `CTF-1` | Cooling tower fan cell 1 | motor via gearbox |

`PLANT.LOAD` is the plant load factor (0.45–1.0), daily cycle with a 16:00 peak and 04:00 trough, lower at weekends, plus slow drift. Every asset's readings move with it, so **normalise for load before comparing periods**.

## Tags

Sampling is hourly. `baseline` is at load 0.75 and full health. `load_gain` is the change per unit of `(load − 0.75)`. `noise` is 1σ white noise.

### GT1 — gas turbine

| Tag | Unit | Baseline | Load gain | Noise | Note |
|---|---|---|---|---|---|
| `GT1.LOAD_MW`    | MW   | 90   | 120 | 0.8  | |
| `GT1.EXH_TEMP`   | °C   | 540  | 60  | 2.0  | |
| `GT1.CDP`        | bar  | 15.5 | 5.0 | 0.08 | compressor discharge pressure |
| `GT1.FUEL_FLOW`  | kg/s | 6.2  | 5.5 | 0.05 | |
| `GT1.VIB_1`      | mm/s | 2.1  | 0.4 | 0.12 | |
| `GT1.BRG_TEMP_1` | °C   | 78   | 6   | 0.6  | |
| `GT1.BRG_TEMP_2` | °C   | 81.4 | 0   | 0    | **dead sensor** — constant, exclude from features |

### BFP1 / BFP2 — boiler feed pumps (identical tag sets)

| Tag | Unit | Baseline | Load gain | Noise | Note |
|---|---|---|---|---|---|
| `BFPx.FLOW`        | t/h  | 260 | 200 | 2.5  | |
| `BFPx.DISCH_PRESS` | bar  | 165 | 18  | 0.7  | |
| `BFPx.VIB_DE`      | mm/s | 1.8 | 0.6 | 0.10 | drive-end bearing |
| `BFPx.VIB_NDE`     | mm/s | 1.5 | 0.5 | 0.10 | non-drive-end bearing |
| `BFPx.BRG_TEMP_DE` | °C   | 62  | 7   | 0.5  | |
| `BFPx.MOTOR_CURR`  | A    | 310 | 180 | 2.0  | |

### CTF1 — cooling tower fan

| Tag | Unit | Baseline | Load gain | Noise | Note |
|---|---|---|---|---|---|
| `CTF1.SPEED`        | rpm  | 118 | 0   | 0.3  | fixed speed |
| `CTF1.VIB`          | mm/s | 2.4 | 0.3 | 0.15 | |
| `CTF1.GBX_OIL_TEMP` | °C   | 58  | 9   | 0.6  | gearbox oil |
| `CTF1.MOTOR_CURR`   | A    | 95  | 25  | 1.0  | |

## Failure modes and symptoms

Health `h` is hidden, 1 = as new, 0 = failed, non-increasing between repairs. A symptom is added to the sensor as `gain × (1 − h)^shape`; degradation accelerates (`h = 1 − x^2.5` over the fault window), so symptoms are faint for most of the window and steep in the last ~20 %. Sign tells direction.

| Mode | Applies to | Symptom tags (gain, shape) | What to look for |
|---|---|---|---|
| `bearing_wear` | BFP1, BFP2 | `VIB_DE` (+4.5, 2.0) · `BRG_TEMP_DE` (+22, 1.5) · `VIB_NDE` (+0.8, 2.0) · `MOTOR_CURR` (+6, 1.0) | DE vibration and DE bearing temperature rising together; NDE barely moves |
| `compressor_fouling` | GT1 | `CDP` (−1.4, 1.0) · `EXH_TEMP` (+28, 1.0) · `FUEL_FLOW` (+0.45, 1.0) · `LOAD_MW` (−5, 1.0) | Linear drift: pressure ratio falls while exhaust temperature and fuel flow rise at the same load |
| `gearbox_wear` | CTF1 | `GBX_OIL_TEMP` (+20, 1.5) · `VIB` (+3.5, 2.0) · `MOTOR_CURR` (+7, 1.0) | Oil temperature leads, vibration follows late |

Non-symptom tags on a degrading asset stay at baseline. Assets do not influence one another.

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

- `corrective_repair` entries appear only once the repair is in the past relative to sim time. After one, the asset's health is 1 again: **reset any degradation features at that timestamp**.
- `timestamp` on a work order is the sim time it was raised. Work orders are created with `POST /maintenance/workorder {"asset_id","type","description","scheduled_for"}` (READ token).
- The log is the only legitimate label source for the modeler. Ground truth (`/admin/ground_truth`, hidden health, `failures()`) is for the validator only — never a model input.

## Time and access

- Sim horizon: 2024-01-01 to 2024-12-31, hourly. Current sim time comes from `GET /clock`; history requests are clamped to it, so nothing after "now" is observable.
- READ token: clock, assets, tags, maintenance. ADMIN token: ground truth, fault injection, reset, model artifacts.
