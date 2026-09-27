# Gearbox wear: cooling tower fan (CTF1)

Gear and bearing wear in the right-angle gearbox between the motor and the fan. Oil temperature
leads; vibration follows late. The fan runs at a fixed ~118 rpm, so speed says nothing about it.

## Symptoms
- `GBX_OIL_TEMP` rising from ~58 °C, up to about +20 °C (friction and wear debris). It moves first.
- `VIB` rising from ~2.4 mm/s, up to about +3.5 mm/s, mostly in the last week before failure.
- `MOTOR_CURR` creeping up a few amps.
- `SPEED` stays at ~118 rpm; a change there is a drive or motor problem, not gearbox wear.
- The risk model flags it via `GBX_OIL_TEMP__slope7d` and `VIB__slope7d`.

## Confirming checks
- Take an oil sample: wear metals (iron, PQ index), particle count, water content, viscosity.
- Check the oil level and look for leaks at the seals and breather; low oil also heats the gearbox.
- Check the oil cooler (if fitted) and airflow around the gearbox.
- Vibration spectrum at the gearbox input and output: gear mesh frequency and sidebands.
- Rule out ambient: compare oil temperature with the air temperature and other fan cells.

## Immediate actions
- Raise an inspection work order and take the oil sample now.
- Top up or change the oil if it is low or degraded; re-check the temperature after a day.
- If wear metals are high or vibration is rising, plan a gearbox inspection with the lid off.
- Reduce the duty of this cell (use the other cells) if oil temperature passes about 85 °C.
- Stage the gearbox bearings and seals, and book a crane if the gearbox must come out.

## Spare parts
- Gearbox bearing set and oil seals.
- Gear oil (full change quantity) and a filter or breather element.
- For heavy wear: gear set, or an exchange gearbox (long lead time; check with the supplier).

## Typical lead time
- From the first oil-temperature rise to failure: about 3 to 4 weeks; vibration climbs mainly in the last week.
- The risk model flags it 10 to 12 days ahead in validation.
- A seal and bearing job takes one to two shifts; an exchange gearbox can take weeks to arrive.
