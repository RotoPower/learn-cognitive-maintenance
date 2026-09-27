# Winding overheat: generator (GEN1)

A stator winding runs hotter than its cooling explains: a blocked cooling path, a failing
strand insulation, or a local hot spot. Partial discharge rises late, as the insulation ages.

## Symptoms
- `STATOR_TEMP_1` (up to ~18 °C) and `STATOR_TEMP_2` (up to ~14 °C) rising at the same MW.
- `COOLANT_TEMP` rising only slightly (~2 °C): the cooler is working, the heat is local.
- `PD_ACTIVITY` (partial discharge) rising late and steeply (up to ~600 pC above baseline).
- `MW` normal: this is not an overload.

## Confirming checks
- Compare stator temperatures at matched MW and coolant temperature over the last 30 days.
- Check the cooling circuit: coolant flow, filters, cooler fouling, fan or pump operation.
- Partial discharge trend and pattern (on-line monitor); a rising, clustered pattern points at insulation damage.
- Check the temperature sensors (RTD) against each other to rule out a failed sensor.

## Immediate actions
- Raise an inspection work order; log stator temperatures and PD every shift.
- Clean or back-flush the cooling path if flow or cooler performance is down.
- Limit load to keep stator temperature within the insulation class limit.
- Plan an electrical inspection (insulation resistance, polarisation index, PD test) at the next outage.
- Stop and inspect if stator temperature passes the alarm limit or PD rises sharply within days.

## Spare parts
- Cooler elements and filters; temperature sensors (RTD).
- Insulation repair materials; for major damage, stator bars (long lead item).

## Typical lead time
- From the first temperature rise to a forced outage: about 3 to 6 weeks.
- Cooling-path cleaning: one shift; insulation repair: days to weeks of outage.
- Lead time of the plant's risk model for this mode: not validated yet (roadmap Phase 3); watch the stator temperature and `PD_ACTIVITY` alerts.
