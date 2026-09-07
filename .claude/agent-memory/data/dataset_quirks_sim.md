---
name: dataset-quirks-sim
description: Exact locations of deliberate data quirks (dead tag, outage, missing hours, duplicate) in data/sim/sensors.csv
metadata:
  type: project
---

`data/sim/sensors.csv` is the hourly historian export (2024-01-01..2024-12-30, wide
table, one column per `<ASSET>.<TAG>` plus `PLANT.LOAD`). It is read-only; there is
no `data/raw` and no `plantctl` CLI in this repo (ignore any prompt text that
references them — verified absent as of 2026-09-08). Ground truth lives in
`data/sim/ground_truth.json` — never read it; only the validator may.

Deliberate quirks (from `plant/faults.yaml`, cross-checked against the CSV):
- **Dead tag**: `GT1.BRG_TEMP_2` constant at 81.400 for the entire horizon.
- **Sensor outage**: all BFP1 tags NaN 2024-07-19T00:00:00 -> 2024-07-21T23:00:00
  (72h, day 200-203 in sim-day terms, event `sensor_outage`).
- **Missing hours**: 5 consecutive hourly rows absent, 2024-05-11T00:00 -> 04:00
  (sim day 131).
- **Duplicated timestamp**: 2024-03-28T14:00:00 appears twice with identical
  values (sim day 87, hour 14).

Injected fault scenarios (health degradation, real signal not a data defect):
- BFP2 `bearing_wear` onset sim-day 240 (2024-08-28), duration 30 days -> symptoms
  in VIB_DE, BRG_TEMP_DE, VIB_NDE, MOTOR_CURR, steepest in the last ~20% of window.
- GT1 `compressor_fouling` onset sim-day 270 (2024-09-27), duration 60 days.
- CTF1 `gearbox_wear` onset sim-day 300 (2024-10-27), duration 25 days.

**How to apply:** [[maintenance-domain]] skill has the symptom gain/shape table.
When profiling any extraction window, check whether it overlaps these dates —
elevated vibration/temp on BFP2 in Aug-Sep 2024 is expected degradation, not
noise to scrub. Always re-verify these dates by scanning the CSV directly
(`plant/faults.yaml` may drift) rather than trusting this memory blindly.

Plant API: `http://127.0.0.1:8000`, READ token `read-token`
(`Authorization: Bearer read-token`). Endpoints: `/assets`, `/clock`,
`/tags/{tag}/history?from=&to=&interval=1h` (clamped to sim "now"),
`/maintenance/log?asset_id=`. ADMIN-only endpoints (`/admin/*`, ground truth,
fault injection, reset) must never be called by the data-prep agent.
