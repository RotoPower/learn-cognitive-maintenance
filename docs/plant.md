# Plant model

Combined-cycle block used by the simulator (`plant/sim.py`): two gas turbines with heat
recovery steam generators, a steam turbine, the generator and step-up transformer, feed and
cooling water pumps, and cooling tower fans. This file is the single source of truth for
asset ids, tags and units; `tests/test_docs.py` checks it against the simulator. Design notes:
`docs/design/full-plant.md`.

## Asset ids

Asset ids in tag names carry **no hyphen** (`GT1.EXH_TEMP`), per CLAUDE.md. The
scenario script `plant/faults.yaml` may spell them with a hyphen (`GT-1`); the
simulator normalises both forms to the canonical id below.

| Canonical id | Also accepted | Asset |
|---|---|---|
| `GT1`   | `GT-1`   | Gas turbine, 120 MW class |
| `BFP1`  | `BFP-1`  | Boiler feed pump A (duty), motor driven |
| `BFP2`  | `BFP-2`  | Boiler feed pump B (duty), motor driven |
| `CTF1`  | `CTF-1`  | Cooling tower fan cell 1, gearbox driven |
| `GT2`   | `GT-2`   | Gas turbine 2, 120 MW class |
| `HRSG1` | `HRSG-1` | Heat recovery steam generator 1 |
| `HRSG2` | `HRSG-2` | Heat recovery steam generator 2 |
| `ST1`   | `ST-1`   | Steam turbine, 55 MW class |
| `BFP3`  | `BFP-3`  | Boiler feed pump C (standby), motor driven |
| `CWP1`  | `CWP-1`  | Cooling water pump 1 |
| `CWP2`  | `CWP-2`  | Cooling water pump 2 |
| `CTF2`  | `CTF-2`  | Cooling tower fan cell 2, gearbox driven |
| `GEN1`  | `GEN-1`  | Generator (block) |
| `TX1`   | `TX-1`   | Main step-up transformer |

The first four are the original MVP plant; their readings are unchanged by the full plant.

## Plant load

A single plant load factor `L(t)` in `[0.45, 1.0]` drives every asset. It has a
daily cycle (afternoon peak), a weekly cycle (weekend low) and slow day-to-day
variation. It is exposed as the tag `PLANT.LOAD` (fraction, 0..1).

## Tags

`baseline` is the value at reference load `L = 0.75` with health = 1.
`load_gain` is the change per unit of `(L - 0.75)`. `noise` is the 1-sigma
white-noise level. 82 tags in all, including `PLANT.LOAD`.

### GT1, GT2 (gas turbines)

| Tag | Unit | Baseline | Load gain | Noise |
|---|---|---|---|---|
| `GTx.LOAD_MW`    | MW   | 90    | 120  | 0.8 |
| `GTx.EXH_TEMP`   | degC | 540   | 60   | 2.0 |
| `GTx.CDP`        | bar  | 15.5  | 5.0  | 0.08 |
| `GTx.FUEL_FLOW`  | kg/s | 6.2   | 5.5  | 0.05 |
| `GTx.VIB_1`      | mm/s | 2.1   | 0.4  | 0.12 |
| `GTx.BRG_TEMP_1` | degC | 78    | 6    | 0.6 |
| `GT1.BRG_TEMP_2` | degC | 81.4  | 0    | 0   | **dead sensor**: transmitter failed, reads a constant |
| `GT2.BRG_TEMP_2` | degC | 80    | 6    | 0.6 |

### HRSG1, HRSG2 (heat recovery steam generators)

| Tag | Unit | Baseline | Load gain | Noise |
|---|---|---|---|---|
| `HRSGx.STEAM_FLOW`  | t/h  | 150 | 140 | 1.5 |
| `HRSGx.FW_FLOW`     | t/h  | 152 | 140 | 1.5 |
| `HRSGx.DRUM_PRESS`  | bar  | 95  | 20  | 0.4 |
| `HRSGx.STACK_TEMP`  | degC | 95  | 12  | 0.8 |
| `HRSGx.MAKEUP_FLOW` | t/h  | 2.0 | 0.5 | 0.15 |

### ST1 (steam turbine)

| Tag | Unit | Baseline | Load gain | Noise |
|---|---|---|---|---|
| `ST1.LOAD_MW`     | MW      | 55  | 70  | 0.6 |
| `ST1.INLET_PRESS` | bar     | 90  | 18  | 0.4 |
| `ST1.INLET_TEMP`  | degC    | 540 | 10  | 1.5 |
| `ST1.STAGE_PRESS` | bar     | 60  | 14  | 0.3 |
| `ST1.EXH_PRESS`   | kPa abs | 8.0 | 2.0 | 0.08 |
| `ST1.VIB_1`       | mm/s    | 1.9 | 0.3 | 0.10 |
| `ST1.BRG_TEMP_1`  | degC    | 75  | 5   | 0.5 |

### BFP1, BFP2, BFP3 (boiler feed pumps)

| Tag | Unit | Baseline | Load gain | Noise |
|---|---|---|---|---|
| `BFPx.FLOW`        | t/h  | 260 | 200 | 2.5 |
| `BFPx.DISCH_PRESS` | bar  | 165 | 18  | 0.7 |
| `BFPx.VIB_DE`      | mm/s | 1.8 | 0.6 | 0.10 |
| `BFPx.VIB_NDE`     | mm/s | 1.5 | 0.5 | 0.10 |
| `BFPx.BRG_TEMP_DE` | degC | 62  | 7   | 0.5 |
| `BFPx.MOTOR_CURR`  | A    | 310 | 180 | 2.0 |

### CWP1, CWP2 (cooling water pumps)

| Tag | Unit | Baseline | Load gain | Noise |
|---|---|---|---|---|
| `CWPx.FLOW`           | m3/h | 9000 | 500 | 40 |
| `CWPx.DISCH_PRESS`    | bar  | 2.5  | 0.2 | 0.02 |
| `CWPx.VIB_DE`         | mm/s | 2.0  | 0.2 | 0.10 |
| `CWPx.VIB_NDE`        | mm/s | 1.7  | 0.2 | 0.10 |
| `CWPx.BRG_TEMP_DE`    | degC | 55   | 3   | 0.5 |
| `CWPx.MOTOR_CURR`     | A    | 180  | 20  | 1.5 |
| `CWPx.SEAL_LEAK_FLOW` | l/h  | 2.0  | 0.2 | 0.2 |

### CTF1, CTF2 (cooling tower fans)

| Tag | Unit | Baseline | Load gain | Noise |
|---|---|---|---|---|
| `CTFx.SPEED`        | rpm  | 118 | 0   | 0.3 |
| `CTFx.VIB`          | mm/s | 2.4 | 0.3 | 0.15 |
| `CTFx.GBX_OIL_TEMP` | degC | 58  | 9   | 0.6 |
| `CTFx.MOTOR_CURR`   | A    | 95  | 25  | 1.0 |

### GEN1 (generator)

| Tag | Unit | Baseline | Load gain | Noise |
|---|---|---|---|---|
| `GEN1.MW`            | MW   | 140 | 190 | 1.0 |
| `GEN1.STATOR_TEMP_1` | degC | 95  | 25  | 0.8 |
| `GEN1.STATOR_TEMP_2` | degC | 96  | 25  | 0.8 |
| `GEN1.COOLANT_TEMP`  | degC | 40  | 6   | 0.4 |
| `GEN1.PD_ACTIVITY`   | pC   | 150 | 30  | 15 |

### TX1 (main step-up transformer)

| Tag | Unit | Baseline | Load gain | Noise |
|---|---|---|---|---|
| `TX1.LOAD_MVA`     | MVA  | 150 | 200 | 1.5 |
| `TX1.TOP_OIL_TEMP` | degC | 60  | 25  | 0.6 |
| `TX1.WINDING_TEMP` | degC | 75  | 35  | 0.8 |
| `TX1.H2_PPM`       | ppm  | 30  | 0   | 1.5 |
| `TX1.MOISTURE_PPM` | ppm  | 10  | 0   | 0.5 |

## Hidden health and failure modes

Every asset has a hidden health index `h` in `[0, 1]` (1 = as new). Between
repairs `h` is non-increasing. A scenario in `plant/faults.yaml` degrades `h`
from 1 at `onset_day` to 0 at `onset_day + duration_days` (the failure), after
which the asset is repaired and `h` returns to 1.

Symptoms are added to the sensor as `gain * (1 - h) ** shape`. A mode can only be
scripted on the assets it applies to (`MODE_ASSETS` in `plant/sim.py`).

| Mode | Applies to | Symptom tags (gain, shape) |
|---|---|---|
| `bearing_wear`       | BFP1, BFP2, BFP3, CWP1, CWP2 | `VIB_DE` (+4.5, 2.0), `BRG_TEMP_DE` (+22, 1.5), `VIB_NDE` (+0.8, 2.0), `MOTOR_CURR` (+6, 1.0) |
| `compressor_fouling` | GT1, GT2     | `CDP` (-1.4, 1.0), `EXH_TEMP` (+28, 1.0), `FUEL_FLOW` (+0.45, 1.0), `LOAD_MW` (-5, 1.0) |
| `gearbox_wear`       | CTF1, CTF2   | `GBX_OIL_TEMP` (+20, 1.5), `VIB` (+3.5, 2.0), `MOTOR_CURR` (+7, 1.0) |
| `tube_leak`          | HRSG1, HRSG2 | `MAKEUP_FLOW` (+6, 1.5), `FW_FLOW` (+6, 1.5), `STACK_TEMP` (-8, 1.5), `DRUM_PRESS` (-2, 1.0) |
| `seal_leak`          | CWP1, CWP2   | `SEAL_LEAK_FLOW` (+25, 1.5), `DISCH_PRESS` (-0.15, 1.0), `FLOW` (-200, 1.0) |
| `blade_erosion`      | ST1          | `LOAD_MW` (-3, 1.0), `STAGE_PRESS` (+2.5, 1.0), `VIB_1` (+1.5, 2.0) |
| `winding_overheat`   | GEN1         | `STATOR_TEMP_1` (+18, 1.5), `STATOR_TEMP_2` (+14, 1.5), `PD_ACTIVITY` (+600, 2.0), `COOLANT_TEMP` (+2, 1.0) |
| `oil_degradation`    | TX1          | `H2_PPM` (+220, 1.5), `MOISTURE_PPM` (+25, 1.0), `TOP_OIL_TEMP` (+4, 1.0) |

**Ground truth rule.** `h`, the `failures()` table and the scenario script are ground
truth. Only the validator may read them; they are never model inputs (CLAUDE.md).

## Events and data quirks

- `sensor_outage` (scenario `event`): every tag of the asset reads NaN between
  `from_day` and `to_day`.
- Dead tag: `GT1.BRG_TEMP_2` is constant for the whole horizon.
- Missing hours: the generated table drops a short block of hours (configured in
  `plant/faults.yaml` under `quirks`).
- Duplicated timestamp: one row appears twice with identical values.

These are deliberate. Real historian exports have all of them, and the data
pipeline is expected to detect and handle them.
