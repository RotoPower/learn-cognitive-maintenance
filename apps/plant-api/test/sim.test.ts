import { describe, expect, it } from "vitest";
import { DEFAULT_FAULTS } from "../src/faults";
import { ASSETS, FAULT_MODES, Plant, TAGS, pyRepr, u01 } from "../src/sim";
import fixture from "./fixtures/parity.json";

const H = 24;
const TOL = 1e-9;

describe("python parity (fixture from scripts/export_parity_fixture.py)", () => {
  for (const fp of fixture.plants) {
    const plant = Plant.fromConfig(DEFAULT_FAULTS, fp.seed);

    it(`seed ${fp.seed}: load()`, () => {
      for (const s of fp.load) expect(plant.load(s.h)).toBeCloseTo(s.v, 9);
    });

    it(`seed ${fp.seed}: health()`, () => {
      for (const s of fp.health) expect(plant.health(s.asset, s.h)).toBeCloseTo(s.v, 12);
    });

    it(`seed ${fp.seed}: value() for every tag`, () => {
      let checked = 0;
      for (const s of fp.values) {
        const v = plant.value(s.asset, s.tag, s.h);
        if (s.v === null) expect(Number.isNaN(v)).toBe(true);
        else expect(Math.abs(v - s.v)).toBeLessThan(TOL * Math.max(1, Math.abs(s.v)));
        checked++;
      }
      expect(checked).toBe(fp.values.length);
    });

    it(`seed ${fp.seed}: failures() and events()`, () => {
      expect(plant.failures()).toEqual(fp.failures);
      expect(plant.events()).toEqual(fp.events);
    });
  }
});

describe("hash primitives", () => {
  it("pyRepr matches Python repr for the key kinds we use", () => {
    expect(pyRepr(4800, "float")).toBe("4800.0");
    expect(pyRepr(1234.5, "float")).toBe("1234.5");
    expect(pyRepr(200, "int")).toBe("200");
    expect(pyRepr("GT1", "str")).toBe("'GT1'");
    expect(pyRepr(0.1 + 0.2, "float")).toBe("0.30000000000000004");
  });
  it("u01 is deterministic and in [0,1)", () => {
    const a = u01("42|'GT1'|'EXH_TEMP'|4800.0|'g1'");
    expect(a).toBe(u01("42|'GT1'|'EXH_TEMP'|4800.0|'g1'"));
    expect(a).toBeGreaterThanOrEqual(0);
    expect(a).toBeLessThan(1);
    expect(a).not.toBe(u01("43|'GT1'|'EXH_TEMP'|4800.0|'g1'"));
  });
});

describe("ported sim tests", () => {
  const plant = Plant.fromConfig(DEFAULT_FAULTS);

  it("same seed, same values; different seed, different noise; script seed-independent", () => {
    const other = Plant.fromConfig(DEFAULT_FAULTS);
    const p43 = Plant.fromConfig(DEFAULT_FAULTS, 43);
    let differs = false;
    for (const t of [0, 1234.5, 8000]) {
      expect(plant.value("GT1", "EXH_TEMP", t)).toBe(other.value("GT1", "EXH_TEMP", t));
      if (Math.abs(plant.value("BFP1", "FLOW", t) - p43.value("BFP1", "FLOW", t)) > 1e-9) differs = true;
    }
    expect(differs).toBe(true);
    expect(p43.failures()).toEqual(plant.failures());
  });

  it("health is monotonic between repairs and hits 0 at failure, 1 after", () => {
    const repairs = plant.failures().map((f) => ({ asset: f.asset, h: plant.toHours(f.repair) }));
    for (const asset of ASSETS) {
      let prev = plant.health(asset, 0);
      for (let h = 1; h < 365 * H; h++) {
        const cur = plant.health(asset, h);
        expect(cur).toBeGreaterThanOrEqual(0);
        expect(cur).toBeLessThanOrEqual(1);
        if (cur > prev + 1e-12) {
          const repaired = repairs.some((r) => r.asset === asset && h - 1 <= r.h && r.h < h);
          expect(repaired, `${asset} health rose at h=${h} without a repair`).toBe(true);
        }
        prev = cur;
      }
    }
    for (const f of plant.failures()) {
      const fail = plant.toHours(f.failure);
      expect(plant.health(f.asset, plant.toHours(f.onset))).toBeCloseTo(1, 12);
      expect(plant.health(f.asset, fail)).toBeCloseTo(0, 12);
      expect(plant.health(f.asset, fail + 1)).toBe(1);
    }
  });

  it("symptoms move in the scripted direction (load-corrected means)", () => {
    const mean = (asset: string, tag: string, from: number, to: number) => {
      const spec = TAGS[asset][tag];
      let s = 0, n = 0;
      for (let h = from; h < to; h++) { s += plant.value(asset, tag, h) - spec.load_gain * (plant.load(h) - 0.75); n++; }
      return s / n;
    };
    for (const f of plant.failures()) {
      const onset = plant.toHours(f.onset), fail = plant.toHours(f.failure);
      for (const [tag, [gain]] of Object.entries(FAULT_MODES[f.mode])) {
        const delta = mean(f.asset, tag, fail - 3 * H, fail) - mean(f.asset, tag, onset - 14 * H, onset);
        expect(Math.sign(delta), `${f.asset}.${tag}`).toBe(Math.sign(gain));
        expect(Math.abs(delta)).toBeGreaterThan(0.4 * Math.abs(gain));
      }
    }
  });

  it("dead tag is constant, outage is NaN", () => {
    expect(plant.value("GT1", "BRG_TEMP_2", 5)).toBe(81.4);
    expect(plant.value("GT1", "BRG_TEMP_2", 8000)).toBe(81.4);
    expect(Number.isNaN(plant.value("BFP1", "FLOW", 201 * H))).toBe(true);
    expect(Number.isNaN(plant.value("BFP1", "FLOW", 203 * H))).toBe(false);
    expect(Number.isNaN(plant.value("BFP2", "FLOW", 201 * H))).toBe(false);
  });

  it("rejects overlapping scenarios and unknown assets", () => {
    expect(() => new Plant({ seed: 1, start: "2024-01-01", horizon_days: 10, scenarios: [
      { asset: "GT1", mode: "compressor_fouling", onset_day: 1, duration_days: 5 },
      { asset: "GT1", mode: "compressor_fouling", onset_day: 3, duration_days: 5 },
    ] })).toThrow(/overlapping/);
    expect(() => plant.value("ZZ9", "X", 0)).toThrow(/unknown asset/);
  });
});
