# Tube leak: heat recovery steam generators (HRSG1, HRSG2)

A leaking tube in the HRSG (economiser, evaporator or superheater) lets water or steam into the
gas path. Water balance is the tell: more water goes in than comes out as steam.

## Symptoms
- `FW_FLOW` (feedwater) running above `STEAM_FLOW` at steady load, the gap widening over days (up to ~6 t/h).
- `MAKEUP_FLOW` climbing from its ~2 t/h baseline (up to ~8 t/h): the cycle is losing water.
- `STACK_TEMP` falling from ~95 °C (up to ~8 °C lower): water in the gas path cools the exhaust.
- `DRUM_PRESS` sagging slightly (~2 bar) at the same load.
- Symptoms are faint at first and steepen in the last days; the other HRSG at the same load is the best baseline.

## Confirming checks
- Water balance over 24 h at steady load: feedwater in minus steam out minus blowdown; a persistent excess points at a leak.
- Compare with the other HRSG over the same hours: both see the same load and ambient.
- Check blowdown and drain valves for passing (a leaking valve also loses water, but does not cool the stack).
- Listen at the casing and check for steam or water at the stack and drains; acoustic leak detection if fitted.
- Rule out instrumentation: cross-check the flow transmitters.

## Immediate actions
- Raise an inspection work order and start the water balance log now.
- Increase make-up water treatment capacity and watch conductivity while the unit runs.
- Plan a controlled shutdown of the affected HRSG (and its gas turbine) before the leak grows: leaks erode neighbouring tubes.
- Stop the unit if make-up water cannot keep the drum level or if the stack temperature drops sharply within hours.
- After shutdown: locate the leak (pressure or dye test), plug or replace the tube, inspect neighbouring tubes for erosion.

## Spare parts
- Tube sections of the affected bank, plugs and welding consumables.
- Header access gaskets and insulation for the repaired area.
- Water treatment chemicals for the extra make-up water.

## Typical lead time
- From first measurable imbalance to a forced outage: about 2 to 4 weeks.
- A tube plug or replacement takes one to three days of outage including cool-down.
- Lead time of the plant's risk model for this mode: not validated yet (roadmap Phase 3); watch the anomaly alerts on `MAKEUP_FLOW` and `FW_FLOW`.
