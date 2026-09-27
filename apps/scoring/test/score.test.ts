import { describe, expect, it } from "vitest";
import fixture from "./fixtures/scoring.json";
import {
  anomalyFlags, assetFeatures, drivers, episodes, hoursSinceRepair, interpretDrivers, makeGrid, ms, predictProba, HOUR_MS,
} from "../src/score";
import type { AnomalyConfig, Flag, PredictArtifact } from "../src/score";

const close = (a: number, b: number, rel = 1e-6, abs = 1e-9) => Math.abs(a - b) <= abs + rel * Math.abs(b);

function fixtureGrid() {
  const first = ms(fixture.first_hour);
  const rows = Object.entries(fixture.readings as Record<string, (number | null)[]>).flatMap(([tag, vals]) =>
    vals.map((value, i) => ({ tag, ts: new Date(first + i * HOUR_MS).toISOString().slice(0, 19), value })),
  );
  return makeGrid(first, ms(fixture.as_of), rows);
}

describe("parity with the Python scorers (scripts/export_scoring_fixture.py)", () => {
  const grid = fixtureGrid();
  const asOf = ms(fixture.as_of);

  it("anomaly flags match models/anomaly.py", () => {
    const got = anomalyFlags(grid, asOf, fixture.anomaly_config as AnomalyConfig);
    const want = fixture.expected.anomaly;
    expect(want.length).toBeGreaterThan(0);
    expect(got.map((f) => [f.tag, f.first_flag_ts, f.last_flag_ts, f.hours_flagged, f.interpretation]))
      .toEqual(want.map((f) => [f.tag, f.first_flag_ts, f.last_flag_ts, f.hours_flagged, f.interpretation]));
    got.forEach((f, i) => {
      expect(close(f.severity, want[i].severity)).toBe(true);
      expect(close(f.z_peak_signed, want[i].z_peak_signed)).toBe(true);
      expect(close(f.z_at_end, want[i].z_at_end)).toBe(true);
    });
  });

  it("feature rows, probabilities and drivers match models/predict", () => {
    const art = fixture.artifact as PredictArtifact;
    const k = Math.round((asOf - grid.first) / HOUR_MS) - new Date(asOf).getUTCHours();
    const at = grid.first + k * HOUR_MS;
    for (const want of fixture.expected.predict) {
      expect(new Date(at).toISOString().slice(0, 19)).toBe(want.features_at);
      const f = assetFeatures(grid, want.asset, k, hoursSinceRepair(want.asset, at, fixture.repairs, ms(fixture.data_start)));
      for (const [name, v] of Object.entries(want.features as Record<string, number | null>)) {
        const g = f[name];
        if (v === null) expect(g === undefined || Number.isNaN(g), `${want.asset} ${name}`).toBe(true);
        else expect(close(g, v, 1e-6, 1e-7), `${want.asset} ${name}: ${g} vs ${v}`).toBe(true);
      }
      expect(close(predictProba(art, f), want.p_fail, 1e-6, 1e-9)).toBe(true);
      const d = drivers(art, f);
      expect(d.map(([n]) => n)).toEqual(want.drivers.map(([n]) => n));
      expect(interpretDrivers(d)).toBe(want.interpretation);
    }
  });
});

describe("episodes", () => {
  const f = (tag: string, first: string, last: string, severity: number): Flag => ({
    asset: tag.split(".")[0], tag, first_flag_ts: first, last_flag_ts: last, hours_flagged: 6, severity, z_peak_signed: severity, z_at_end: 1, interpretation: `s${severity}`,
  });
  it("merges runs of one tag less than 24 h apart and keeps the strongest peak", () => {
    const e = episodes([
      f("BFP2.VIB_DE", "2024-09-15T00:00:00", "2024-09-15T06:00:00", 4),
      f("BFP2.VIB_DE", "2024-09-15T20:00:00", "2024-09-16T02:00:00", 6),
      f("BFP2.VIB_DE", "2024-09-18T00:00:00", "2024-09-18T06:00:00", 3.5),
      f("BFP2.BRG_TEMP_DE", "2024-09-15T01:00:00", "2024-09-15T07:00:00", 3.2),
    ]);
    expect(e.map((x) => [x.tag, x.first_flag_ts, x.last_flag_ts, x.hours_flagged, x.severity, x.interpretation])).toEqual([
      ["BFP2.BRG_TEMP_DE", "2024-09-15T01:00:00", "2024-09-15T07:00:00", 6, 3.2, "s3.2"],
      ["BFP2.VIB_DE", "2024-09-15T00:00:00", "2024-09-16T02:00:00", 12, 6, "s6"],
      ["BFP2.VIB_DE", "2024-09-18T00:00:00", "2024-09-18T06:00:00", 6, 3.5, "s3.5"],
    ]);
  });
});
