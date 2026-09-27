// Mirror of plant/faults.yaml. Keep in sync; test/sim.test.ts checks the resulting
// failures() table against the Python fixture.
import type { PlantConfig } from "./sim";

export const DEFAULT_FAULTS: PlantConfig = {
  seed: 42,
  start: "2024-01-01T00:00:00",
  horizon_days: 731, // 2024-01-01 .. 2025-12-31 (docs/design/full-plant.md)
  scenarios: [
    // 2024: the original demo story, unchanged
    { asset: "BFP-2", mode: "bearing_wear", onset_day: 240, duration_days: 30 },
    { asset: "GT-1", mode: "compressor_fouling", onset_day: 270, duration_days: 60 },
    { asset: "CTF-1", mode: "gearbox_wear", onset_day: 300, duration_days: 25 },
    { asset: "BFP-1", event: "sensor_outage", from_day: 200, to_day: 203 },
    // 2024: full plant
    { asset: "TX1", mode: "oil_degradation", onset_day: 40, duration_days: 130 },
    { asset: "CWP1", mode: "seal_leak", onset_day: 95, duration_days: 25 },
    { asset: "HRSG1", mode: "tube_leak", onset_day: 180, duration_days: 20 },
    { asset: "HRSG2", event: "sensor_outage", from_day: 350, to_day: 352 },
    // 2025
    { asset: "GT2", mode: "compressor_fouling", onset_day: 380, duration_days: 55 },
    { asset: "ST1", mode: "blade_erosion", onset_day: 410, duration_days: 100 },
    { asset: "GEN1", mode: "winding_overheat", onset_day: 470, duration_days: 30 },
    { asset: "BFP3", mode: "bearing_wear", onset_day: 520, duration_days: 30 },
    { asset: "CWP2", mode: "seal_leak", onset_day: 560, duration_days: 25 },
    { asset: "HRSG2", mode: "tube_leak", onset_day: 600, duration_days: 18 },
    { asset: "CTF2", mode: "gearbox_wear", onset_day: 630, duration_days: 25 },
    { asset: "GT1", mode: "compressor_fouling", onset_day: 640, duration_days: 60 },
    { asset: "CWP1", mode: "bearing_wear", onset_day: 660, duration_days: 35 },
  ],
};
