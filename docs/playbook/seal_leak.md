# Seal leak: cooling water pumps (CWP1, CWP2)

The mechanical seal (or packing) of a cooling water pump wears and leaks. The leak-off
measurement leads; hydraulic performance sags only slightly. CWP1 and CWP2 can also suffer
bearing wear: tell the two apart by which tags move.

## Symptoms
- `SEAL_LEAK_FLOW` rising from its ~2 l/h baseline, steeply towards the end (up to ~25 l/h more).
- `DISCH_PRESS` sagging slightly (up to ~0.15 bar) and `FLOW` dropping (up to ~200 m3/h) at the same load.
- `VIB_DE` and `BRG_TEMP_DE` stay near baseline: if they rise, it is bearing wear, not the seal.

## Confirming checks
- Inspect the seal area: visible leakage, spray, wet baseplate, leak-off drain flow.
- Compare with the other cooling water pump at the same load.
- Check the seal flush or quench supply (pressure, flow, filter) and the gland cooling water.
- Check shaft alignment and vibration: misalignment and high vibration shorten seal life.

## Immediate actions
- Raise an inspection work order; log leak-off flow twice a day.
- Keep the leak-off drain clear so leaking water does not reach the motor or bearings.
- Plan a seal change on the standby principle: start the other pump, isolate this one.
- Stop the pump if the leak sprays onto the motor or bearing housings, or if leak-off flow rises sharply within a day.
- When replacing the seal, check shaft sleeve wear and alignment.

## Spare parts
- Mechanical seal cartridge (or packing set) for the pump model.
- Shaft sleeve and O-rings.
- Seal flush filter elements.

## Typical lead time
- From first rise in leak-off flow to an unacceptable leak: about 3 to 4 weeks.
- A cartridge seal change takes one shift with the pump isolated.
- Lead time of the plant's risk model for this mode: not validated yet (roadmap Phase 3); watch the anomaly alerts on `SEAL_LEAK_FLOW`.
