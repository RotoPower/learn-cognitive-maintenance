import { describe, expect, it } from "vitest";
import fixture from "./fixtures/scoring.json";
import {
  anomalyFlags, assetFeatures, drivers, episodes, interpretDrivers, makeGrid, ms, predictProba, segmentStart, HOUR_MS,
} from "../src/score";
import type { AnomalyConfig, Flag, PredictArtifact } from "../src/score";

const close = (a: number, b: number, rel = 1e-6, abs = 1e-9) => Math.abs(a - b) <= abs + rel * Math.abs(b);
const art = fixture.artifact as PredictArtifact;
const readings = fixture.readings as Record<string, (number | null)[]>;

/** The grid the Worker would build for one case: LOOKBACK_DAYS ending at as_of. */
function caseGrid(asOf: number) {
  const unionFirst = ms(fixture.first_hour);
  const first = asOf - (fixture.lookback_days * 24 - 1) * HOUR_MS;
  const rows = Object.entries(readings).flatMap(([tag, vals]) =>
    vals.map((value, i) => ({ tag, ts: new Date(unionFirst + i * HOUR_MS).toISOString().slice(0, 19), value }))
      .filter((r) => ms(r.ts) >= first && ms(r.ts) <= asOf),
  );
  return makeGrid(first, asOf, rows);
}

describe.each(fixture.cases)("parity with the Python scorers as of $as_of", (c) => {
  const asOf = ms(c.as_of);
  const grid = caseGrid(asOf);
  const repairs = fixture.repairs.filter((r) => ms(r.timestamp) <= asOf);

  it("anomaly flags match models/anomaly.py", () => {
    const got = anomalyFlags(grid, asOf, fixture.anomaly_config as AnomalyConfig);
    const want = c.expected.anomaly;
    expect(want.length).toBeGreaterThan(0);
    expect(got.map((f) => [f.tag, f.first_flag_ts, f.last_flag_ts, f.hours_flagged, f.interpretation]))
      .toEqual(want.map((f) => [f.tag, f.first_flag_ts, f.last_flag_ts, f.hours_flagged, f.interpretation]));
    got.forEach((f, i) => {
      expect(close(f.severity, want[i].severity)).toBe(true);
      expect(close(f.z_peak_signed, want[i].z_peak_signed)).toBe(true);
      expect(close(f.z_at_end, want[i].z_at_end)).toBe(true);
    });
  });

  it("features (windows reset at repairs), probabilities, 2-day alerts and drivers match models/predict", () => {
    const k = Math.round((asOf - grid.first) / HOUR_MS) - new Date(asOf).getUTCHours();
    const feats = (asset: string, kk: number) => assetFeatures(grid, asset, kk, 0, segmentStart(grid, asset, kk, repairs));
    for (const want of c.expected.predict) {
      expect(new Date(grid.first + k * HOUR_MS).toISOString().slice(0, 19)).toBe(want.features_at);
      const f = feats(want.asset, k);
      for (const [name, v] of Object.entries(want.features as Record<string, number | null>)) {
        const g = f[name];
        if (v === null) expect(g === undefined || Number.isNaN(g), `${want.asset} ${name}`).toBe(true);
        else expect(close(g, v, 1e-6, 1e-7), `${want.asset} ${name}: ${g} vs ${v}`).toBe(true);
      }
      const p = predictProba(art, f);
      expect(close(p, want.p_fail, 1e-6, 1e-9)).toBe(true);
      const prev = want.p_prev.map((_, j) => predictProba(art, feats(want.asset, k - 24 * (want.p_prev.length - j))));
      prev.forEach((x, j) => expect(close(x, want.p_prev[j], 1e-6, 1e-9)).toBe(true));
      expect([...prev, p].every((x) => x >= art.threshold)).toBe(want.alert);
      const d = drivers(art, f);
      expect(d.map(([n]) => n)).toEqual(want.drivers.map(([n]) => n));
      expect(interpretDrivers(d)).toBe(want.interpretation);
    }
  });
});

it("the fixture covers both sides of a repair", () => {
  const [before, after] = fixture.cases;
  const bfp2 = (c: typeof before) => c.expected.predict.find((p) => p.asset === "BFP2")!;
  expect(bfp2(before).alert).toBe(true);
  expect(bfp2(after).alert).toBe(false);
  expect(fixture.repairs.some((r) => r.asset_id === "BFP2" && ms(r.timestamp) < ms(after.as_of))).toBe(true);
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
