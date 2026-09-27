# Bearing wear: boiler feed pumps (BFP1, BFP2)

Drive-end (DE) bearing degradation on a motor-driven boiler feed pump. Readings move with plant
load, so compare at the same `PLANT.LOAD` (baselines below are at load 0.75).

## Symptoms
- `VIB_DE` rising from its ~1.8 mm/s baseline; late and steep (it grows with damage squared), reaching ~6 mm/s near failure.
- `BRG_TEMP_DE` rising from ~62 °C, earlier and steadier than vibration; ~84 °C near failure.
- `VIB_DE` and `BRG_TEMP_DE` rise **together**; `VIB_NDE` barely moves (+0.8 mm/s at most), which points at the drive end.
- `MOTOR_CURR` creeps up a few amps (friction); `FLOW` and `DISCH_PRESS` stay normal, so it is not a process problem.
- Anomaly alerts on `VIB_DE` / `BRG_TEMP_DE`, or a failure risk above threshold with `VIB_DE__slope7d` / `BRG_TEMP_DE__slope7d` as top drivers.

## Confirming checks
- Compare BFP1 and BFP2 over the same hours: the healthy pump at the same load is the best baseline.
- Rule out load: confirm the rise persists at comparable `PLANT.LOAD` (e.g. the daily 16:00 peak on several days).
- Portable vibration spectrum at the DE bearing: bearing defect frequencies and their harmonics, rising high-frequency envelope.
- Check the DE bearing temperature with a handheld probe to rule out a sensor fault.
- Inspect lubrication: grease quantity and interval, contamination or discoloration; check for leaks at the seal.
- Against ISO 10816-3 (group 2, rigid): above 2.8 mm/s is zone C (restricted operation), above 4.5 mm/s zone D (damage likely).

## Immediate actions
- Raise an inspection work order on the pump and schedule the vibration spectrum within 24 hours.
- Re-grease the DE bearing per the lubrication chart; re-check vibration and temperature after 24 hours.
- Shift duty to the standby pump when `VIB_DE` passes 2.8 mm/s at normal load, and plan the bearing change.
- Stop the pump if `VIB_DE` passes 4.5 mm/s or `BRG_TEMP_DE` passes 85 °C, or if both rise sharply within a day.
- Stage the bearing kit and book a maintenance window before the predicted failure date.

## Spare parts
- DE bearing set (bearing, lock nut and washer, shims) for the pump model.
- Mechanical seal kit (change with the bearing if the pump is opened).
- Grease per the lubrication chart; bearing heater and puller.
- Coupling insert, if the coupling is removed for alignment.

## Typical lead time
- From first detectable rise to failure: about 4 to 5 weeks. Most damage shows in the last week, when vibration climbs steeply.
- The risk model flags it 13 to 16 days ahead in validation; the anomaly detector about 11 days ahead.
- A bearing change with alignment takes one shift; keep a bearing set in stores (supplier lead time is often 2 to 4 weeks).
