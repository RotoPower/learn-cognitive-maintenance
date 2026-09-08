// Mirror of plant/faults.yaml. Keep in sync; test/sim.test.ts checks the resulting
// failures() table against the Python fixture.
import type { PlantConfig } from "./sim";

export const DEFAULT_FAULTS: PlantConfig = {
  seed: 42,
  start: "2024-01-01T00:00:00",
  horizon_days: 365,
  scenarios: [
    { asset: "BFP-2", mode: "bearing_wear", onset_day: 240, duration_days: 30 },
    { asset: "GT-1", mode: "compressor_fouling", onset_day: 270, duration_days: 60 },
    { asset: "CTF-1", mode: "gearbox_wear", onset_day: 300, duration_days: 25 },
    { asset: "BFP-1", event: "sensor_outage", from_day: 200, to_day: 203 },
  ],
};
