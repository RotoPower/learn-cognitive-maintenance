# Oil degradation: main step-up transformer (TX1)

The insulating oil ages and takes up moisture; slow thermal or electrical stress produces
dissolved gases. It develops over months and the temperature barely moves, so dissolved gas
and moisture are the signals to watch.

## Symptoms
- `H2_PPM` (dissolved hydrogen) climbing from ~30 ppm, steepening late (up to ~220 ppm more).
- `MOISTURE_PPM` rising steadily from ~10 ppm (up to ~25 ppm more).
- `TOP_OIL_TEMP` a few degrees higher at the same load (up to ~4 °C): not the main signal.
- `LOAD_MVA` and `WINDING_TEMP` normal.

## Confirming checks
- Take an oil sample for full dissolved gas analysis (DGA), moisture and breakdown voltage.
- Interpret DGA with a standard method (key gases, Duval triangle, IEC 60599); hydrogen alone points at partial discharge or oil decomposition.
- Check the breather (silica gel colour), conservator and gaskets for moisture ingress.
- Compare the trend with the last laboratory samples.

## Immediate actions
- Raise an inspection work order and increase DGA sampling (weekly while rising).
- Replace the breather desiccant; fix any seal or gasket leak letting moisture in.
- Plan oil treatment (filtration and drying) or an oil change on the laboratory result.
- Reduce load and plan an outage if gases rise quickly or acetylene appears in the DGA.

## Spare parts
- Breather desiccant, gasket set.
- Treated transformer oil for top-up or change; filtration unit (rental).

## Typical lead time
- Develops over 3 to 5 months; hydrogen rises fastest in the last weeks.
- Oil treatment can often be done on line; an oil change needs an outage of a few days.
- Lead time of the plant's risk model for this mode: not validated yet (roadmap Phase 3); watch `H2_PPM` and `MOISTURE_PPM`.
