/**
 * Pure scoring math, ported from the Python scorers so the Worker needs no Python:
 *
 *  - anomaly: models/anomaly.py. Per tag, a 30-day rolling mean/std over the hours
 *    STRICTLY before each hour (window closed on the left), z = (v - mean) / std, and a
 *    flag for every run of >= min_run_hours consecutive hours with |z| > z_threshold.
 *  - predict: models/predict/features.py + model.py. One feature row per asset at the
 *    sim day's 00:00 (<TAG>__cur, __mean7d, __slope7d, __slope30d, LOAD__cur/mean7d,
 *    hours_since_repair), standardised with the artefact's scaler (NaN -> 0, neutral),
 *    then the logistic score and the top positive drivers.
 *
 * Series live on a contiguous hourly grid; a missing hour is NaN, which is equivalent to
 * the Python code's "gap in the index" (both break runs and never enter a window).
 * Parity with Python is tested against test/fixtures/scoring.json
 * (scripts/export_scoring_fixture.py).
 */

export const HOUR_MS = 3_600_000;

export const iso = (ms: number): string => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "");
export const ms = (isoNaive: string): number => Date.parse(/Z$|[+-]\d\d:\d\d$/.test(isoNaive) ? isoNaive : `${isoNaive}Z`);

/** Hourly grid ending at asOf (inclusive): values[tag][i] is the reading at first + i hours. */
export interface Grid {
  first: number; // ms of index 0
  n: number;
  values: Record<string, Float64Array>; // full tag name -> NaN where missing / outage
}

export function makeGrid(firstMs: number, asOfMs: number, rows: { tag: string; ts: string; value: number | null }[]): Grid {
  const n = Math.round((asOfMs - firstMs) / HOUR_MS) + 1;
  const values: Record<string, Float64Array> = {};
  for (const r of rows) {
    const a = (values[r.tag] ??= new Float64Array(n).fill(NaN));
    const i = Math.round((ms(r.ts) - firstMs) / HOUR_MS);
    if (i >= 0 && i < n && r.value !== null) a[i] = r.value;
  }
  return { first: firstMs, n, values };
}

/** A tag with at most one distinct value in the window is dead (constant transmitter). */
export function isDead(x: Float64Array): boolean {
  let first = NaN;
  for (const v of x) {
    if (Number.isNaN(v)) continue;
    if (Number.isNaN(first)) first = v;
    else if (v !== first) return false;
  }
  return true;
}

// ------------------------------------------------------------------ anomaly

export interface AnomalyConfig {
  baseline_days: number;
  min_periods_hours: number;
  z_threshold: number;
  min_run_hours: number;
  window_days: number;
  target_assets: string[];
}

export const DEFAULT_ANOMALY: AnomalyConfig = {
  baseline_days: 30, min_periods_hours: 480, z_threshold: 3, min_run_hours: 6, window_days: 7,
  target_assets: ["GT1", "BFP1", "BFP2", "CTF1"],
};

/** z-score of each hour against the `hours` hours strictly before it (ddof = 1). */
export function zscores(x: Float64Array, hours: number, minPeriods: number): Float64Array {
  const n = x.length;
  const z = new Float64Array(n).fill(NaN);
  let c = NaN; // shift for numerical stability of the running sums
  for (const v of x) if (!Number.isNaN(v)) { c = v; break; }
  if (Number.isNaN(c)) return z;
  let cnt = 0, s = 0, ss = 0;
  for (let i = 0; i < n; i++) {
    // window for i is [i - hours, i - 1]: add i - 1, drop i - hours - 1
    const add = i - 1, drop = i - hours - 1;
    if (add >= 0 && !Number.isNaN(x[add])) { const d = x[add] - c; cnt++; s += d; ss += d * d; }
    if (drop >= 0 && !Number.isNaN(x[drop])) { const d = x[drop] - c; cnt--; s -= d; ss -= d * d; }
    if (cnt < Math.max(minPeriods, 2) || Number.isNaN(x[i])) continue;
    const mean = s / cnt;
    const variance = Math.max((ss - cnt * mean * mean) / (cnt - 1), 0);
    const std = Math.sqrt(variance);
    if (std > 0) z[i] = (x[i] - c - mean) / std;
  }
  return z;
}

export interface Flag {
  asset: string;
  tag: string;
  first_flag_ts: string;
  last_flag_ts: string;
  hours_flagged: number;
  severity: number;
  z_peak_signed: number;
  z_at_end: number;
  interpretation: string;
}

const FAILURE_MODES: Record<string, [string, number, string][]> = {
  "BFP.VIB_DE": [["bearing_wear", +1, "DE vibration rising: primary bearing_wear symptom"]],
  "BFP.BRG_TEMP_DE": [["bearing_wear", +1, "DE bearing temperature rising: bearing_wear symptom"]],
  "BFP.VIB_NDE": [["bearing_wear", +1, "NDE vibration rising: weak bearing_wear symptom (gain 0.8)"]],
  "BFP.MOTOR_CURR": [["bearing_wear", +1, "motor current rising: secondary bearing_wear symptom"]],
  "GT1.CDP": [["compressor_fouling", -1, "CDP falling: compressor_fouling symptom"]],
  "GT1.EXH_TEMP": [["compressor_fouling", +1, "exhaust temperature rising: compressor_fouling symptom"]],
  "GT1.FUEL_FLOW": [["compressor_fouling", +1, "fuel flow rising at load: compressor_fouling symptom"]],
  "GT1.LOAD_MW": [["compressor_fouling", -1, "MW output falling: compressor_fouling symptom"]],
  "CTF1.GBX_OIL_TEMP": [["gearbox_wear", +1, "gearbox oil temperature rising: leading gearbox_wear symptom"]],
  "CTF1.VIB": [["gearbox_wear", +1, "fan vibration rising: late gearbox_wear symptom"]],
  "CTF1.MOTOR_CURR": [["gearbox_wear", +1, "motor current rising: secondary gearbox_wear symptom"]],
};

export function interpretAnomaly(asset: string, tag: string, zSigned: number): string {
  const short = tag.split(".", 2)[1];
  const family = asset.startsWith("BFP") ? "BFP" : asset;
  const modes = FAILURE_MODES[`${family}.${short}`];
  const direction = zSigned > 0 ? "up" : "down";
  if (!modes) return `${short} ${direction}: not a symptom tag of any documented failure mode`;
  for (const [, sign, note] of modes) if (Math.sign(zSigned) === sign) return note;
  return `${short} ${direction}: direction opposite to ${modes[0][0]} symptom; no documented mode matches`;
}

/** Flags whose run intersects the scoring window (as_of - window_days, as_of]. */
export function anomalyFlags(grid: Grid, asOfMs: number, cfg: AnomalyConfig): Flag[] {
  const winStart = asOfMs - cfg.window_days * 24 * HOUR_MS;
  const tags = Object.keys(grid.values)
    .filter((t) => cfg.target_assets.includes(t.split(".", 1)[0]) && !isDead(grid.values[t]))
    .sort();
  const out: Flag[] = [];
  for (const tag of tags) {
    const asset = tag.split(".", 1)[0];
    const z = zscores(grid.values[tag], cfg.baseline_days * 24, cfg.min_periods_hours);
    let i = 0;
    while (i < grid.n) {
      if (!(Math.abs(z[i]) > cfg.z_threshold)) { i++; continue; }
      let j = i;
      while (j + 1 < grid.n && Math.abs(z[j + 1]) > cfg.z_threshold) j++;
      const n = j - i + 1;
      const first = grid.first + i * HOUR_MS, last = grid.first + j * HOUR_MS;
      if (n >= cfg.min_run_hours && last >= winStart && first <= asOfMs) {
        let k = i;
        for (let m = i; m <= j; m++) if (Math.abs(z[m]) > Math.abs(z[k])) k = m;
        out.push({
          asset, tag, first_flag_ts: iso(first), last_flag_ts: iso(last), hours_flagged: n,
          severity: Math.abs(z[k]), z_peak_signed: z[k], z_at_end: z[j],
          interpretation: interpretAnomaly(asset, tag, z[k]),
        });
      }
      i = j + 1;
    }
  }
  return out;
}

/** Merge runs of the same tag that are < gapHours apart into one episode. */
export function episodes(flags: Flag[], gapHours = 24): Flag[] {
  const out: Flag[] = [];
  const sorted = [...flags].sort((a, b) => (a.tag === b.tag ? a.first_flag_ts.localeCompare(b.first_flag_ts) : a.tag < b.tag ? -1 : 1));
  for (const f of sorted) {
    const prev = out[out.length - 1];
    if (prev && prev.tag === f.tag && ms(f.first_flag_ts) - ms(prev.last_flag_ts) < gapHours * HOUR_MS) {
      prev.last_flag_ts = f.last_flag_ts;
      prev.hours_flagged += f.hours_flagged;
      prev.z_at_end = f.z_at_end;
      if (f.severity > prev.severity) {
        prev.severity = f.severity;
        prev.z_peak_signed = f.z_peak_signed;
        prev.interpretation = f.interpretation;
      }
    } else out.push({ ...f });
  }
  return out;
}

// ------------------------------------------------------------------ predict

export interface PredictArtifact {
  run_id: string;
  feature_names: string[];
  scaler: { mean: number[]; std: number[] };
  coefficients: number[];
  intercept: number;
  threshold: number;
  horizon_days: number;
  /** Training config: persistence = consecutive daily scores above threshold for an alert;
   *  reset_at_repairs = feature windows never reach back across a corrective repair. */
  config?: { persistence?: number; reset_at_repairs?: boolean; step_hours?: number };
}

export const LOAD_TAG = "PLANT.LOAD";

function windowStats(x: Float64Array, k: number, len: number, lo = 0): { cnt: number; mean: number; slope: number } {
  // window (t_k - len h, t_k] = indices k - len + 1 .. k, never before `lo` (a repair); time in hours relative to k
  const from = Math.max(0, lo, k - len + 1);
  let cnt = 0, sx = 0, st = 0;
  for (let i = from; i <= k; i++) if (!Number.isNaN(x[i])) { cnt++; sx += x[i]; st += i - k; }
  if (cnt === 0) return { cnt, mean: NaN, slope: NaN };
  const mx = sx / cnt, mt = st / cnt;
  let sxt = 0, stt = 0;
  for (let i = from; i <= k; i++) if (!Number.isNaN(x[i])) { const dt = i - k - mt; sxt += (x[i] - mx) * dt; stt += dt * dt; }
  const variance = stt / cnt;
  return { cnt, mean: mx, slope: variance > 1e-9 ? (sxt / cnt / variance) * 24 : NaN };
}

/** Grid index of the first hour at or after the asset's latest repair at or before index k
 *  (0 when none): feature windows reset there, as in build_features(reset_at_repairs=True). */
export function segmentStart(grid: Grid, asset: string, k: number, repairs: { asset_id: string; timestamp: string }[]): number {
  const at = grid.first + k * HOUR_MS;
  let last = -Infinity;
  for (const r of repairs) {
    const t = ms(r.timestamp);
    if (r.asset_id === asset && t <= at && t > last) last = t;
  }
  return last === -Infinity ? 0 : Math.max(0, Math.ceil((last - grid.first) / HOUR_MS));
}

/** Feature row for one asset at grid index k (the sim day's 00:00); windows start no earlier than `lo`. */
export function assetFeatures(grid: Grid, asset: string, k: number, hoursSinceRepair: number, lo = 0): Record<string, number> {
  const f: Record<string, number> = {};
  const w7 = 7 * 24, w30 = 30 * 24;
  for (const [tag, x] of Object.entries(grid.values)) {
    if (tag.split(".", 1)[0] !== asset || isDead(x)) continue;
    const short = tag.split(".", 2)[1];
    const a = windowStats(x, k, w7, lo), b = windowStats(x, k, w30, lo);
    f[`${short}__cur`] = x[k];
    f[`${short}__mean7d`] = a.cnt >= 96 ? a.mean : NaN;
    f[`${short}__slope7d`] = a.cnt >= 96 ? a.slope : NaN;
    f[`${short}__slope30d`] = b.cnt >= 360 ? b.slope : NaN;
  }
  const load = grid.values[LOAD_TAG];
  if (load) {
    const a = windowStats(load, k, w7, lo);
    f["LOAD__cur"] = load[k];
    f["LOAD__mean7d"] = a.cnt >= 96 ? a.mean : NaN;
  }
  f["hours_since_repair"] = hoursSinceRepair;
  return f;
}

export function hoursSinceRepair(asset: string, atMs: number, repairs: { asset_id: string; timestamp: string }[], dataStartMs: number): number {
  let last = dataStartMs;
  for (const r of repairs) {
    const t = ms(r.timestamp);
    if (r.asset_id === asset && t <= atMs && t > last) last = t;
  }
  return (atMs - last) / HOUR_MS;
}

const sigmoid = (z: number) => 1 / (1 + Math.exp(-Math.min(Math.max(z, -30), 30)));

export function standardise(art: PredictArtifact, features: Record<string, number>): number[] {
  return art.feature_names.map((name, j) => {
    const v = features[name];
    const z = (v - art.scaler.mean[j]) / art.scaler.std[j];
    return v === undefined || Number.isNaN(z) ? 0 : z; // missing tag or window -> neutral
  });
}

export function predictProba(art: PredictArtifact, features: Record<string, number>): number {
  const z = standardise(art, features);
  return sigmoid(z.reduce((acc, v, j) => acc + v * art.coefficients[j], art.intercept));
}

/** Top-k positive contributions z_j * coef_j. */
export function drivers(art: PredictArtifact, features: Record<string, number>, k = 3): [string, number][] {
  const z = standardise(art, features);
  return z
    .map((v, j) => [art.feature_names[j], v * art.coefficients[j]] as [string, number])
    .sort((a, b) => b[1] - a[1])
    .slice(0, k)
    .filter(([, c]) => c > 0);
}

const SYMPTOMS: Record<string, string> = {
  VIB_DE: "bearing_wear", BRG_TEMP_DE: "bearing_wear", VIB_NDE: "bearing_wear",
  CDP: "compressor_fouling", EXH_TEMP: "compressor_fouling", FUEL_FLOW: "compressor_fouling", LOAD_MW: "compressor_fouling",
  GBX_OIL_TEMP: "gearbox_wear", VIB: "gearbox_wear",
};

export function interpretDrivers(d: [string, number][]): string {
  const modes = new Map<string, string[]>();
  for (const [name] of d) {
    const tag = name.split("__")[0];
    const mode = SYMPTOMS[tag];
    if (mode) modes.set(mode, [...(modes.get(mode) ?? []), tag]);
  }
  if (modes.size === 0) return "no documented failure-mode symptom among top drivers";
  let best: [string, string[]] | null = null;
  for (const e of modes) if (!best || e[1].length > best[1].length) best = e;
  return `consistent with ${best![0]} (${[...new Set(best![1])].join(", ")})`;
}
