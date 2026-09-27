/**
 * Deterministic plant simulator: TypeScript port of plant/sim.py.
 *
 * Every sensor value is a pure function of (seed, asset, tag, sim_time):
 *   value = baseline + symptom(1 - health) + load_gain * (load - REF_LOAD) + noise
 *
 * Randomness is blake2b over the same key string Python builds
 * ("|".join(repr(part))), so a Worker and the Python sim agree to ~1e-12.
 * Ground truth (health, failures) is admin-only, exactly as in Python.
 */
import { blake2b } from "@noble/hashes/blake2.js";

export const REF_LOAD = 0.75;
export const PLANT_LOAD_TAG = "PLANT.LOAD";
export const HEALTH_SHAPE = 2.5;

export interface TagSpec {
  baseline: number;
  load_gain: number;
  noise: number;
  dead?: boolean;
}

const PUMP_TAGS: Record<string, TagSpec> = {
  FLOW: { baseline: 260.0, load_gain: 200.0, noise: 2.5 },
  DISCH_PRESS: { baseline: 165.0, load_gain: 18.0, noise: 0.7 },
  VIB_DE: { baseline: 1.8, load_gain: 0.6, noise: 0.1 },
  VIB_NDE: { baseline: 1.5, load_gain: 0.5, noise: 0.1 },
  BRG_TEMP_DE: { baseline: 62.0, load_gain: 7.0, noise: 0.5 },
  MOTOR_CURR: { baseline: 310.0, load_gain: 180.0, noise: 2.0 },
};

const GT_TAGS: Record<string, TagSpec> = {
  LOAD_MW: { baseline: 90.0, load_gain: 120.0, noise: 0.8 },
  EXH_TEMP: { baseline: 540.0, load_gain: 60.0, noise: 2.0 },
  CDP: { baseline: 15.5, load_gain: 5.0, noise: 0.08 },
  FUEL_FLOW: { baseline: 6.2, load_gain: 5.5, noise: 0.05 },
  VIB_1: { baseline: 2.1, load_gain: 0.4, noise: 0.12 },
  BRG_TEMP_1: { baseline: 78.0, load_gain: 6.0, noise: 0.6 },
};

const CTF_TAGS: Record<string, TagSpec> = {
  SPEED: { baseline: 118.0, load_gain: 0.0, noise: 0.3 },
  VIB: { baseline: 2.4, load_gain: 0.3, noise: 0.15 },
  GBX_OIL_TEMP: { baseline: 58.0, load_gain: 9.0, noise: 0.6 },
  MOTOR_CURR: { baseline: 95.0, load_gain: 25.0, noise: 1.0 },
};

const HRSG_TAGS: Record<string, TagSpec> = {
  STEAM_FLOW: { baseline: 150.0, load_gain: 140.0, noise: 1.5 },
  FW_FLOW: { baseline: 152.0, load_gain: 140.0, noise: 1.5 },
  DRUM_PRESS: { baseline: 95.0, load_gain: 20.0, noise: 0.4 },
  STACK_TEMP: { baseline: 95.0, load_gain: 12.0, noise: 0.8 },
  MAKEUP_FLOW: { baseline: 2.0, load_gain: 0.5, noise: 0.15 },
};

const CWP_TAGS: Record<string, TagSpec> = {
  FLOW: { baseline: 9000.0, load_gain: 500.0, noise: 40.0 },
  DISCH_PRESS: { baseline: 2.5, load_gain: 0.2, noise: 0.02 },
  VIB_DE: { baseline: 2.0, load_gain: 0.2, noise: 0.1 },
  VIB_NDE: { baseline: 1.7, load_gain: 0.2, noise: 0.1 },
  BRG_TEMP_DE: { baseline: 55.0, load_gain: 3.0, noise: 0.5 },
  MOTOR_CURR: { baseline: 180.0, load_gain: 20.0, noise: 1.5 },
  SEAL_LEAK_FLOW: { baseline: 2.0, load_gain: 0.2, noise: 0.2 },
};

// Mirror of plant/sim.py. The original four assets come first and never change.
export const TAGS: Record<string, Record<string, TagSpec>> = {
  GT1: { ...GT_TAGS, BRG_TEMP_2: { baseline: 81.4, load_gain: 0.0, noise: 0.0, dead: true } },
  BFP1: { ...PUMP_TAGS },
  BFP2: { ...PUMP_TAGS },
  CTF1: { ...CTF_TAGS },
  // full plant (docs/design/full-plant.md)
  GT2: { ...GT_TAGS, BRG_TEMP_2: { baseline: 80.0, load_gain: 6.0, noise: 0.6 } },
  HRSG1: { ...HRSG_TAGS },
  HRSG2: { ...HRSG_TAGS },
  ST1: {
    LOAD_MW: { baseline: 55.0, load_gain: 70.0, noise: 0.6 },
    INLET_PRESS: { baseline: 90.0, load_gain: 18.0, noise: 0.4 },
    INLET_TEMP: { baseline: 540.0, load_gain: 10.0, noise: 1.5 },
    STAGE_PRESS: { baseline: 60.0, load_gain: 14.0, noise: 0.3 },
    EXH_PRESS: { baseline: 8.0, load_gain: 2.0, noise: 0.08 },
    VIB_1: { baseline: 1.9, load_gain: 0.3, noise: 0.1 },
    BRG_TEMP_1: { baseline: 75.0, load_gain: 5.0, noise: 0.5 },
  },
  BFP3: { ...PUMP_TAGS },
  CWP1: { ...CWP_TAGS },
  CWP2: { ...CWP_TAGS },
  CTF2: { ...CTF_TAGS },
  GEN1: {
    MW: { baseline: 140.0, load_gain: 190.0, noise: 1.0 },
    STATOR_TEMP_1: { baseline: 95.0, load_gain: 25.0, noise: 0.8 },
    STATOR_TEMP_2: { baseline: 96.0, load_gain: 25.0, noise: 0.8 },
    COOLANT_TEMP: { baseline: 40.0, load_gain: 6.0, noise: 0.4 },
    PD_ACTIVITY: { baseline: 150.0, load_gain: 30.0, noise: 15.0 },
  },
  TX1: {
    LOAD_MVA: { baseline: 150.0, load_gain: 200.0, noise: 1.5 },
    TOP_OIL_TEMP: { baseline: 60.0, load_gain: 25.0, noise: 0.6 },
    WINDING_TEMP: { baseline: 75.0, load_gain: 35.0, noise: 0.8 },
    H2_PPM: { baseline: 30.0, load_gain: 0.0, noise: 1.5 },
    MOISTURE_PPM: { baseline: 10.0, load_gain: 0.0, noise: 0.5 },
  },
};
export const ASSETS = Object.keys(TAGS);

export const ASSET_INFO: Record<string, string> = {
  GT1: "Gas turbine, 120 MW class",
  BFP1: "Boiler feed pump A (duty), motor driven",
  BFP2: "Boiler feed pump B (duty), motor driven",
  CTF1: "Cooling tower fan cell 1, gearbox driven",
  GT2: "Gas turbine 2, 120 MW class",
  HRSG1: "Heat recovery steam generator 1",
  HRSG2: "Heat recovery steam generator 2",
  ST1: "Steam turbine, 55 MW class",
  BFP3: "Boiler feed pump C (standby), motor driven",
  CWP1: "Cooling water pump 1",
  CWP2: "Cooling water pump 2",
  CTF2: "Cooling tower fan cell 2, gearbox driven",
  GEN1: "Generator (block)",
  TX1: "Main step-up transformer",
};

// mode -> tag -> [gain, shape]; symptom = gain * (1 - health) ** shape
export const FAULT_MODES: Record<string, Record<string, [number, number]>> = {
  bearing_wear: { VIB_DE: [4.5, 2.0], BRG_TEMP_DE: [22.0, 1.5], VIB_NDE: [0.8, 2.0], MOTOR_CURR: [6.0, 1.0] },
  compressor_fouling: { CDP: [-1.4, 1.0], EXH_TEMP: [28.0, 1.0], FUEL_FLOW: [0.45, 1.0], LOAD_MW: [-5.0, 1.0] },
  gearbox_wear: { GBX_OIL_TEMP: [20.0, 1.5], VIB: [3.5, 2.0], MOTOR_CURR: [7.0, 1.0] },
  tube_leak: { MAKEUP_FLOW: [6.0, 1.5], FW_FLOW: [6.0, 1.5], STACK_TEMP: [-8.0, 1.5], DRUM_PRESS: [-2.0, 1.0] },
  seal_leak: { SEAL_LEAK_FLOW: [25.0, 1.5], DISCH_PRESS: [-0.15, 1.0], FLOW: [-200.0, 1.0] },
  blade_erosion: { LOAD_MW: [-3.0, 1.0], STAGE_PRESS: [2.5, 1.0], VIB_1: [1.5, 2.0] },
  winding_overheat: { STATOR_TEMP_1: [18.0, 1.5], STATOR_TEMP_2: [14.0, 1.5], PD_ACTIVITY: [600.0, 2.0], COOLANT_TEMP: [2.0, 1.0] },
  oil_degradation: { H2_PPM: [220.0, 1.5], MOISTURE_PPM: [25.0, 1.0], TOP_OIL_TEMP: [4.0, 1.0] },
};

// Which assets each failure mode can happen on (a scenario elsewhere is rejected).
export const MODE_ASSETS: Record<string, string[]> = {
  bearing_wear: ["BFP1", "BFP2", "BFP3", "CWP1", "CWP2"],
  compressor_fouling: ["GT1", "GT2"],
  gearbox_wear: ["CTF1", "CTF2"],
  tube_leak: ["HRSG1", "HRSG2"],
  seal_leak: ["CWP1", "CWP2"],
  blade_erosion: ["ST1"],
  winding_overheat: ["GEN1"],
  oil_degradation: ["TX1"],
};

export function canonicalAsset(name: string): string {
  const key = name.replace(/[-_]/g, "").toUpperCase();
  if (!(key in TAGS)) throw new Error(`unknown asset '${name}'; known: ${ASSETS.join(", ")}`);
  return key;
}

export function splitTag(full: string): [string, string] {
  const i = full.indexOf(".");
  if (i < 0 || i === full.length - 1) throw new Error(`tag must look like ASSET.TAG, got '${full}'`);
  return [full.slice(0, i), full.slice(i + 1)];
}

// ----------------------------------------------------------------------------
// Hash-based randomness, byte-compatible with Python's _u01 / _gauss
// ----------------------------------------------------------------------------

/** Python repr() for the value kinds that appear in hash keys. */
export function pyRepr(v: number | string, kind: "int" | "float" | "str"): string {
  if (kind === "str") return `'${v}'`;
  const n = v as number;
  if (kind === "int") return String(Math.trunc(n));
  // float: shortest round-trip (same algorithm as JS) but Python prints "4800.0" for integers
  // and switches to exponent form outside [1e-4, 1e16).
  if (Number.isInteger(n) && Math.abs(n) < 1e16) return `${n}.0`;
  const s = String(n);
  if (s.includes("e")) return s.replace(/e([+-])(\d)$/, "e$10$2"); // 1e-5 -> 1e-05 like Python
  return s;
}

const enc = new TextEncoder();

/** Uniform in [0, 1) depending only on the key string. */
export function u01(key: string): number {
  const digest = blake2b(enc.encode(key), { dkLen: 8 });
  let x = 0n;
  for (let i = 7; i >= 0; i--) x = (x << 8n) | BigInt(digest[i]); // little-endian u64
  return Number(x >> 11n) / 2 ** 53;
}

export function gauss(key: string): number {
  let u1 = u01(`${key}|'g1'`);
  const u2 = u01(`${key}|'g2'`);
  u1 = Math.max(u1, 1e-300);
  return Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2);
}

// ----------------------------------------------------------------------------
// Scenario script
// ----------------------------------------------------------------------------

export interface ScenarioItem {
  asset: string;
  mode?: string;
  onset_day?: number;
  duration_days?: number;
  event?: "sensor_outage";
  from_day?: number;
  to_day?: number;
}

export interface PlantConfig {
  seed: number;
  start: string; // ISO, naive (treated as UTC)
  horizon_days: number;
  scenarios: ScenarioItem[];
}

export interface Scenario {
  asset: string;
  mode: string;
  onset_h: number;
  duration_h: number;
  failure_h: number;
}

export interface Outage {
  asset: string;
  from_h: number;
  to_h: number;
}

export interface FailureRecord {
  asset: string;
  mode: string;
  onset: string;
  failure: string;
  repair: string;
}

export function parseNaiveIso(s: string): Date {
  // "2024-01-01" or "2024-01-01T00:00[:00]" -> UTC instant (the sim has no time zone)
  const t = s.length <= 10 ? `${s}T00:00:00Z` : s.endsWith("Z") ? s : `${s}Z`;
  const d = new Date(t);
  if (Number.isNaN(d.getTime())) throw new Error(`bad timestamp '${s}'`);
  return d;
}

export function isoNaive(d: Date): string {
  return d.toISOString().replace(/\.\d{3}Z$/, "");
}

export class Plant {
  readonly seed: number;
  readonly start: Date;
  readonly horizonH: number;
  readonly scenarios: Scenario[];
  readonly outages: Outage[];
  private readonly byAsset: Record<string, Scenario[]>;

  constructor(cfg: PlantConfig) {
    this.seed = Math.trunc(cfg.seed);
    this.start = parseNaiveIso(cfg.start);
    this.horizonH = cfg.horizon_days * 24;
    const scenarios: Scenario[] = [];
    const outages: Outage[] = [];
    for (const item of cfg.scenarios) {
      const asset = canonicalAsset(item.asset);
      if (item.mode !== undefined) {
        if (!(item.mode in FAULT_MODES)) throw new Error(`unknown fault mode '${item.mode}'`);
        if (!MODE_ASSETS[item.mode].includes(asset))
          throw new Error(`${item.mode} does not apply to ${asset}; it applies to ${MODE_ASSETS[item.mode].join(", ")}`);
        const onset_h = (item.onset_day as number) * 24;
        const duration_h = (item.duration_days as number) * 24;
        scenarios.push({ asset, mode: item.mode, onset_h, duration_h, failure_h: onset_h + duration_h });
      } else if (item.event === "sensor_outage") {
        outages.push({ asset, from_h: (item.from_day as number) * 24, to_h: (item.to_day as number) * 24 });
      } else {
        throw new Error(`cannot interpret scenario entry ${JSON.stringify(item)}`);
      }
    }
    scenarios.sort((a, b) => (a.asset < b.asset ? -1 : a.asset > b.asset ? 1 : a.onset_h - b.onset_h));
    this.scenarios = scenarios;
    this.outages = outages;
    this.byAsset = {};
    for (const a of ASSETS) this.byAsset[a] = scenarios.filter((s) => s.asset === a);
    for (const a of ASSETS) {
      const ss = this.byAsset[a];
      for (let i = 1; i < ss.length; i++) if (ss[i].onset_h < ss[i - 1].failure_h) throw new Error(`overlapping scenarios on ${a}`);
    }
  }

  static fromConfig(cfg: PlantConfig, seed?: number): Plant {
    return new Plant(seed === undefined ? cfg : { ...cfg, seed });
  }

  // ----- time -----------------------------------------------------------------

  toHours(t: Date | number | string): number {
    if (typeof t === "number") return t;
    const d = typeof t === "string" ? parseNaiveIso(t) : t;
    return (d.getTime() - this.start.getTime()) / 3_600_000;
  }

  toDate(hours: number): Date {
    return new Date(this.start.getTime() + hours * 3_600_000);
  }

  get end(): Date {
    return this.toDate(this.horizonH);
  }

  tags(): string[] {
    const out = [PLANT_LOAD_TAG];
    for (const a of ASSETS) for (const t of Object.keys(TAGS[a])) out.push(`${a}.${t}`);
    return out;
  }

  // ----- hidden state ---------------------------------------------------------

  /** Plant load factor in [0.45, 1.0]: daily + weekly cycle + slow drift. */
  load(t: Date | number | string): number {
    const h = this.toHours(t);
    const day = h / 24.0;
    const hour = h % 24.0;
    const d0 = Math.floor(day);
    const frac = day - d0;
    const daily = 0.5 * (1.0 - Math.cos((2.0 * Math.PI * (hour - 4.0)) / 24.0));
    const startWeekday = (this.start.getUTCDay() + 6) % 7; // Python: Monday = 0
    const dow = (startWeekday + d0) % 7;
    const weekend = dow >= 5 ? -0.1 : 0.0;
    const j0 = u01(`${this.seed}|'PLANT'|'LOAD'|${pyRepr(d0, "int")}`);
    const j1 = u01(`${this.seed}|'PLANT'|'LOAD'|${pyRepr(d0 + 1, "int")}`);
    const slow = 0.08 * (2.0 * ((1.0 - frac) * j0 + frac * j1) - 1.0);
    return Math.min(1.0, Math.max(0.45, 0.55 + 0.3 * daily + weekend + slow));
  }

  activeScenario(asset: string, t: Date | number | string): Scenario | null {
    const a = canonicalAsset(asset);
    const h = this.toHours(t);
    for (const s of this.byAsset[a]) if (s.onset_h <= h && h <= s.failure_h) return s;
    return null;
  }

  /** GROUND TRUTH. Hidden health in [0, 1]; 1 = as new, 0 = failed. */
  health(asset: string, t: Date | number | string): number {
    const s = this.activeScenario(asset, t);
    if (!s) return 1.0;
    const x = (this.toHours(t) - s.onset_h) / s.duration_h;
    return Math.max(0.0, 1.0 - Math.pow(x, HEALTH_SHAPE));
  }

  inOutage(asset: string, t: Date | number | string): boolean {
    const a = canonicalAsset(asset);
    const h = this.toHours(t);
    return this.outages.some((o) => o.asset === a && o.from_h <= h && h < o.to_h);
  }

  // ----- sensors --------------------------------------------------------------

  /** Sensor reading. Pure in (seed, asset, tag, t). NaN during an outage. */
  value(asset: string, tag: string, t: Date | number | string): number {
    if (asset.toUpperCase() === "PLANT" && tag === "LOAD") return this.load(t);
    const a = canonicalAsset(asset);
    const spec = TAGS[a][tag];
    if (!spec) throw new Error(`unknown tag ${a}.${tag}`);
    if (spec.dead) return spec.baseline;
    if (this.inOutage(a, t)) return Number.NaN;

    const h = this.toHours(t);
    const loadTerm = spec.load_gain * (this.load(h) - REF_LOAD);

    let symptom = 0.0;
    const s = this.activeScenario(a, h);
    if (s) {
      const gs = FAULT_MODES[s.mode][tag];
      if (gs) symptom = gs[0] * Math.pow(1.0 - this.health(a, h), gs[1]);
    }
    const noise = spec.noise * gauss(`${this.seed}|'${a}'|'${tag}'|${pyRepr(h, "float")}`);
    return spec.baseline + symptom + loadTerm + noise;
  }

  valueByTag(fullTag: string, t: Date | number | string): number {
    const [a, tag] = splitTag(fullTag);
    return this.value(a, tag, t);
  }

  // ----- ground truth ---------------------------------------------------------

  /** GROUND TRUTH. One record per scripted failure. Validator use only. */
  failures(): FailureRecord[] {
    return [...this.scenarios]
      .sort((x, y) => x.onset_h - y.onset_h)
      .map((s) => ({
        asset: s.asset,
        mode: s.mode,
        onset: isoNaive(this.toDate(s.onset_h)),
        failure: isoNaive(this.toDate(s.failure_h)),
        repair: isoNaive(this.toDate(s.failure_h)),
      }));
  }

  events(): { asset: string; event: string; from: string; to: string }[] {
    return this.outages.map((o) => ({
      asset: o.asset,
      event: "sensor_outage",
      from: isoNaive(this.toDate(o.from_h)),
      to: isoNaive(this.toDate(o.to_h)),
    }));
  }
}
