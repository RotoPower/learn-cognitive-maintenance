# Blade erosion: steam turbine (ST1)

Solid particles or water droplets erode the turbine blades and nozzles. Efficiency falls slowly:
the same steam produces less power. Vibration appears only late, when erosion unbalances a stage.

## Symptoms
- `LOAD_MW` falling at the same inlet conditions and plant load (up to ~3 MW).
- `STAGE_PRESS` (first stage) rising at the same flow (up to ~2.5 bar): flow area has changed.
- `VIB_1` rising late and steeply (up to ~1.5 mm/s).
- `INLET_PRESS`, `INLET_TEMP` and `EXH_PRESS` stay normal: the steam supply and the condenser are not the cause.
- A slow drift over weeks to months; compare at matched load, never week against week blindly.

## Confirming checks
- Trend corrected output (MW per unit of steam flow) at matched inlet conditions over the last 60 days.
- Stage pressure survey: first-stage and extraction pressures against the design curve.
- Check steam purity (silica, sodium, iron) and the attemperator spray: both feed erosion.
- Borescope the accessible stages at the next opportunity.

## Immediate actions
- Raise an inspection work order and start weekly performance tests at a fixed load.
- Review steam chemistry and attemperation practice to stop further erosion.
- Plan a blade inspection at the next planned outage; a vibration step change brings it forward.
- Reduce load or stop if `VIB_1` rises quickly or passes the trip-warning limit.

## Spare parts
- Replacement blades or nozzle segments for the affected stage (long lead item; order early).
- Seal strips and shims for re-blading.
- Balancing weights.

## Typical lead time
- Efficiency loss develops over 2 to 4 months; vibration only in the last weeks.
- Blade supply can take months: order on confirmation, repair at a planned outage.
- Lead time of the plant's risk model for this mode: not validated yet (roadmap Phase 3); watch `STAGE_PRESS` and `LOAD_MW` trends.
