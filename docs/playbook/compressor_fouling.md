# Compressor fouling: gas turbine (GT1)

Deposits on the compressor blades (dust, salt, oil mist) that the inlet filters let through.
Performance falls slowly and almost linearly; compare at the same load and ambient conditions.

## Symptoms
- `CDP` (compressor discharge pressure) drifting down from ~15.5 bar, up to about 1.4 bar lower.
- `EXH_TEMP` drifting up from ~540 °C, up to about +28 °C.
- `FUEL_FLOW` rising at the same load, up to about +0.45 kg/s: the machine burns more fuel for the same output.
- `LOAD_MW` falling at the same plant load, up to about 5 MW lost.
- All four drift together and slowly over weeks; `VIB_1` and `BRG_TEMP_1` stay normal.
- The anomaly detector can miss it (its rolling baseline absorbs a slow drift); the risk model flags it via `CDP__slope7d`, `EXH_TEMP__slope7d` and `FUEL_FLOW__slope7d`.

## Confirming checks
- Trend corrected output and heat rate at matched load over the last 30 days; a steady decline with rising exhaust temperature points at fouling.
- Check the inlet filter differential pressure and the filter change history.
- Rule out instrumentation: compare `CDP` and `EXH_TEMP` with redundant readings or the control system.
- Borescope the first compressor stages at the next opportunity to see the deposits.
- Record the last compressor wash (online and offline) dates.

## Immediate actions
- Schedule an online compressor wash; compare `CDP`, `EXH_TEMP` and `FUEL_FLOW` before and after.
- If the online wash recovers little, plan an offline (crank) wash at the next shutdown or weekend low load.
- Inspect and replace inlet filters if their differential pressure is high.
- Keep exhaust temperature within the control limit; reduce load if the margin is small.
- Raise a work order for the wash and log the recovered performance.

## Spare parts
- Compressor wash detergent and demineralised water for online and offline washes.
- Inlet filter elements (pre-filters and final filters).
- Nothing structural is replaced for fouling alone.

## Typical lead time
- A slow drift: roughly 7 to 9 weeks from first detectable change to unacceptable performance.
- The risk model flags it 24 to 27 days ahead in validation, enough to plan an offline wash into a planned shutdown.
- An online wash takes about an hour at load; an offline wash needs a shutdown of one shift.
