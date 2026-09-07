# Plant model

Small combined-cycle block used by the simulator (`plant/sim.py`). This file is the
single source of truth for asset ids, tags and units.

## Asset ids

Asset ids in tag names carry **no hyphen** (`GT1.EXH_TEMP`), per CLAUDE.md. The
scenario script `plant/faults.yaml` may spell them with a hyphen (`GT-1`); the
simulator normalises both forms to the canonical id below.

| Canonical id | Also accepted | Asset |
|---|---|---|
| `GT1`  | `GT-1`  | Gas turbine, 120 MW class |
| `BFP1` | `BFP-1` | Boiler feed pump A (duty), motor driven |
| `BFP2` | `BFP-2` | Boiler feed pump B (duty), motor driven |
| `CTF1` | `CTF-1` | Cooling tower fan cell 1, gearbox driven |

## Plant load

A single plant load factor `L(t)` in `[0.45, 1.0]` drives every asset. It has a
daily cycle (afternoon peak), a weekly cycle (weekend low) and slow day-to-day
variation. It is exposed as the tag `PLANT.LOAD` (fraction, 0..1).

## Tags

`baseline` is the value at reference load `L = 0.75` with health = 1.
`load_gain` is the change per unit of `(L - 0.75)`. `noise` is the 1-sigma
white-noise level.

### GT1 (gas turbine)

| Tag | Unit | Baseline | Load gain | Noise |
|---|---|---|---|---|
| `GT1.LOAD_MW`    | MW   | 90    | 120  | 0.8 |
| `GT1.EXH_TEMP`   | degC | 540   | 60   | 2.0 |
| `GT1.CDP`        | bar  | 15.5  | 5.0  | 0.08 |
| `GT1.FUEL_FLOW`  | kg/s | 6.2   | 5.5  | 0.05 |
| `GT1.VIB_1`      | mm/s | 2.1   | 0.4  | 0.12 |
| `GT1.BRG_TEMP_1` | degC | 78    | 6    | 0.6 |
| `GT1.BRG_TEMP_2` | degC | 81.4  | 0    | 0   | **dead sensor**: transmitter failed, reads a constant |

### BFP1 / BFP2 (boiler feed pumps)

| Tag | Unit | Baseline | Load gain | Noise |
|---|---|---|---|---|
| `BFPx.FLOW`        | t/h  | 260 | 200 | 2.5 |
| `BFPx.DISCH_PRESS` | bar  | 165 | 18  | 0.7 |
| `BFPx.VIB_DE`      | mm/s | 1.8 | 0.6 | 0.10 |
| `BFPx.VIB_NDE`     | mm/s | 1.5 | 0.5 | 0.10 |
| `BFPx.BRG_TEMP_DE` | degC | 62  | 7   | 0.5 |
| `BFPx.MOTOR_CURR`  | A    | 310 | 180 | 2.0 |

### CTF1 (cooling tower fan)

| Tag | Unit | Baseline | Load gain | Noise |
|---|---|---|---|---|
| `CTF1.SPEED`        | rpm  | 118 | 0    | 0.3 |
| `CTF1.VIB`          | mm/s | 2.4 | 0.3  | 0.15 |
| `CTF1.GBX_OIL_TEMP` | degC | 58  | 9    | 0.6 |
| `CTF1.MOTOR_CURR`   | A    | 95  | 25   | 1.0 |

## Hidden health and failure modes

Every asset has a hidden health index `h` in `[0, 1]` (1 = as new). Between
repairs `h` is non-increasing. A scenario in `plant/faults.yaml` degrades `h`
from 1 at `onset_day` to 0 at `onset_day + duration_days` (the failure), after
which the asset is repaired and `h` returns to 1.

Symptoms are added to the sensor as `gain * (1 - h) ** shape`.

| Mode | Symptom tags (gain, shape) |
|---|---|
| `bearing_wear` (pumps)     | `VIB_DE` (+4.5, 2.0), `BRG_TEMP_DE` (+22, 1.5), `VIB_NDE` (+0.8, 2.0), `MOTOR_CURR` (+6, 1.0) |
| `compressor_fouling` (GT)  | `CDP` (-1.4, 1.0), `EXH_TEMP` (+28, 1.0), `FUEL_FLOW` (+0.45, 1.0), `LOAD_MW` (-5, 1.0) |
| `gearbox_wear` (CT fan)    | `GBX_OIL_TEMP` (+20, 1.5), `VIB` (+3.5, 2.0), `MOTOR_CURR` (+7, 1.0) |

**Ground truth rule.** `h` and the `failures()` table are ground truth. Only the
validator may read them; they are never model inputs (CLAUDE.md).

## Events and data quirks

- `sensor_outage` (scenario `event`): every tag of the asset reads NaN between
  `from_day` and `to_day`.
- Dead tag: `GT1.BRG_TEMP_2` is constant for the whole horizon.
- Missing hours: the generated table drops a short block of hours (configured in
  `plant/faults.yaml` under `quirks`).
- Duplicated timestamp: one row appears twice with identical values.

These are deliberate. Real historian exports have all of them, and the data
pipeline is expected to detect and handle them.
