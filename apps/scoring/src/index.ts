/**
 * Scoring Worker (module D2.4).
 *
 * Cron `0 * * * *`: at clock speed 60 one real hour is one sim day, so this scores once
 * per sim day ("nightly" in plant time). A pass is skipped when that sim day is already
 * scored, so a paused or clamped clock costs one indexed lookup.
 *
 * One pass, as of the sim clock's current hour:
 *  1. reads the latest artefact per task from D1 `model_artifacts` (anomaly: config;
 *     predict: scaler + coefficients), overridable with ANOMALY_ARTIFACT / PREDICT_ARTIFACT;
 *  2. reads LOOKBACK_DAYS of hourly `readings` per tag through the (tag, ts) primary key,
 *     the asset list and the maintenance log (corrective repairs) from the plant API;
 *  3. computes anomaly z-score flags and failure probabilities (src/score.ts);
 *  4. writes `runs`, `predictions`, and `alerts` (runs of the same tag < 24 h apart are
 *     one episode; an open alert is extended rather than duplicated).
 *  5. closes what a corrective repair resolved: open alerts that started before the
 *     asset's latest repair become 'resolved', and so do its open scoring work orders
 *     ('done'); an episode that ended at a repair is recorded as already resolved.
 * Predict alerts and work orders (`maintenance_log`, source='scoring') are raised only when
 * PREDICT_ACTIONS=on: the current predict model is NO-GO, so staging keeps it off.
 *
 * D1 budget: ~24 tags x LOOKBACK_DAYS x 24 rows read per pass (~35k at 60 days), every
 * query index-bounded; see README.
 *
 * HTTP: GET /health (last run) is open; POST /score[?force=1] needs the ADMIN token.
 */

import {
  DEFAULT_ANOMALY, HOUR_MS, anomalyFlags, assetFeatures, drivers, episodes, hoursSinceRepair,
  PREDICT_ASSETS, interpretDrivers, iso, makeGrid, ms, predictProba, segmentStart,
} from "./score";
import type { AnomalyConfig, Flag, PredictArtifact } from "./score";

export interface Env {
  DB: D1Database;
  PLANT_API_URL: string;
  /** Service Binding to the plant API Worker (staging/production); see apps/ingest. */
  PLANT?: { fetch(input: string | Request, init?: RequestInit): Promise<Response> };
  READ_TOKEN?: string;
  ADMIN_TOKEN?: string;
  LOOKBACK_DAYS?: string;
  /** Origin of hours_since_repair for assets with no logged repair (the training table's start). */
  DATA_START?: string;
  /** "on" to raise predict alerts and work orders; anything else records predictions only. */
  PREDICT_ACTIONS?: string;
  ANOMALY_ARTIFACT?: string;
  PREDICT_ARTIFACT?: string;
}

export type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

const UA = "plant-scoring/0.1 (+https://github.com/RotoPower/learn-cognitive-maintenance)";

function transport(env: Env, injected?: Fetcher): Fetcher {
  if (injected) return injected;
  if (env.PLANT) return (url, init) => env.PLANT!.fetch(url, init);
  return (url, init) => fetch(url, init);
}

async function api<T>(env: Env, fetcher: Fetcher, path: string): Promise<T> {
  const r = await fetcher(`${env.PLANT_API_URL.replace(/\/+$/, "")}${path}`, {
    headers: { authorization: `Bearer ${env.READ_TOKEN ?? "read-token"}`, "user-agent": UA, accept: "application/json" },
  });
  if (!r.ok) throw new Error(`plant API ${path} -> HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return (await r.json()) as T;
}

interface StoredArtifact { run_id: string; model?: Record<string, unknown>; meta?: Record<string, unknown> }

async function artifact(env: Env, task: string, pinned: string | undefined): Promise<StoredArtifact | null> {
  const row = pinned
    ? await env.DB.prepare("SELECT body FROM model_artifacts WHERE run_id = ?").bind(pinned).first<{ body: string }>()
    : await env.DB.prepare("SELECT body FROM model_artifacts WHERE task = ? ORDER BY uploaded_at DESC LIMIT 1").bind(task).first<{ body: string }>();
  return row ? (JSON.parse(row.body) as StoredArtifact) : null;
}

function predictModel(a: StoredArtifact): PredictArtifact {
  const m = a.model as unknown as PredictArtifact;
  return { ...m, run_id: a.run_id, horizon_days: Number((a.meta?.horizon_days as number) ?? 30), config: m.config ?? {} };
}

function anomalyConfig(a: StoredArtifact | null): AnomalyConfig {
  const cfg = (a?.model as { config?: Partial<AnomalyConfig> } | undefined)?.config ?? {};
  return { ...DEFAULT_ANOMALY, ...cfg };
}

export interface ScoreResult {
  skipped?: string;
  as_of: string;
  sim_day: string;
  anomaly?: { artifact: string | null; flags: number; episodes: number; alerts_opened: number; alerts_extended: number };
  predict?: { artifact: string; features_at: string; p_fail: Record<string, number>; alerts: number; workorders: number } | { skipped: string };
  rows_read: number;
}

/** One scoring pass. `fetcher` is injectable for tests. */
export async function scoreOnce(env: Env, opts: { force?: boolean; fetcher?: Fetcher } = {}): Promise<ScoreResult> {
  const fetcher = transport(env, opts.fetcher);
  const clock = await api<{ sim_time: string }>(env, fetcher, "/clock");
  const asOfMs = Math.floor(ms(clock.sim_time) / HOUR_MS) * HOUR_MS;
  const asOf = iso(asOfMs);
  const simDay = asOf.slice(0, 10);
  const runIds = { anomaly: `score_${simDay}_anomaly`, predict: `score_${simDay}_predict` };
  let rowsRead = 0;
  const read = <T>(r: D1Result<T>) => { rowsRead += r.meta?.rows_read ?? 0; return r.results; };

  if (!opts.force) {
    const done = await env.DB.prepare("SELECT run_id FROM runs WHERE run_id = ?").bind(runIds.anomaly).first();
    if (done) return { skipped: `sim day ${simDay} already scored`, as_of: asOf, sim_day: simDay, rows_read: 1 };
  }
  const started = new Date().toISOString();

  // ---- inputs
  const [anomalyArt, predictArt] = await Promise.all([
    artifact(env, "anomaly", env.ANOMALY_ARTIFACT),
    artifact(env, "predict", env.PREDICT_ARTIFACT),
  ]);
  const assets = await api<{ asset_id: string; tags: string[] }[]>(env, fetcher, "/assets");
  const log = await api<{ kind: string; asset_id: string; timestamp: string }[]>(env, fetcher, "/maintenance/log");
  const repairs = log.filter((e) => e.kind === "corrective_repair" && ms(e.timestamp) <= asOfMs);
  const lastRepair = (asset: string): number | null => {
    const ts = repairs.filter((r) => r.asset_id === asset).map((r) => ms(r.timestamp));
    return ts.length ? Math.max(...ts) : null;
  };

  const lookbackH = Number(env.LOOKBACK_DAYS ?? 60) * 24;
  const firstMs = asOfMs - (lookbackH - 1) * HOUR_MS;
  const tags = [...new Set(assets.flatMap((a) => a.tags))].sort();
  const stmt = env.DB.prepare("SELECT tag, ts, value FROM readings WHERE tag = ? AND ts >= ? AND ts <= ?");
  const results = await env.DB.batch<{ tag: string; ts: string; value: number | null }>(tags.map((t) => stmt.bind(t, iso(firstMs), asOf)));
  const grid = makeGrid(firstMs, asOfMs, results.flatMap((r) => read(r)));

  const writes: D1PreparedStatement[] = [];
  const out: ScoreResult = { as_of: asOf, sim_day: simDay, rows_read: 0 };

  // ---- anomaly
  // Score every asset the plant lists, not the (older, shorter) list stored in the artefact.
  const cfg = { ...anomalyConfig(anomalyArt), target_assets: assets.map((a) => a.asset_id).filter((a) => a !== "PLANT") };
  const flags = anomalyFlags(grid, asOfMs, cfg);
  const eps = episodes(flags);
  let opened = 0, extended = 0, resolved = 0;
  for (const e of eps) {
    // An episode that a repair ended is history: record it resolved, never reopen it.
    const endedByRepair = repairs.some((r) => r.asset_id === e.asset && ms(e.first_flag_ts) < ms(r.timestamp) && ms(e.last_flag_ts) <= ms(r.timestamp));
    const open = await env.DB.prepare(
      "SELECT id, last_flag_ts, severity FROM alerts WHERE asset_id = ? AND kind = 'anomaly' AND status = 'open' AND tag = ? ORDER BY last_flag_ts DESC LIMIT 1",
    ).bind(e.asset, e.tag).first<{ id: number; last_flag_ts: string; severity: number }>();
    rowsRead += open ? 1 : 0;
    if (endedByRepair) {
      const known = await env.DB.prepare("SELECT id FROM alerts WHERE asset_id = ? AND kind = 'anomaly' AND tag = ? AND first_flag_ts <= ? AND last_flag_ts >= ? LIMIT 1")
        .bind(e.asset, e.tag, e.last_flag_ts, e.first_flag_ts).first<{ id: number }>();
      if (!known) { writes.push(insertAlert(env, runIds.anomaly, e, "resolved")); resolved++; }
    } else if (open && ms(e.first_flag_ts) - ms(open.last_flag_ts) < 24 * HOUR_MS) {
      writes.push(env.DB.prepare("UPDATE alerts SET last_flag_ts = MAX(last_flag_ts, ?), severity = MAX(severity, ?), run_id = ?, interpretation = CASE WHEN ? > severity THEN ? ELSE interpretation END WHERE id = ?")
        .bind(e.last_flag_ts, e.severity, runIds.anomaly, e.severity, e.interpretation, open.id));
      extended++;
    } else {
      writes.push(insertAlert(env, runIds.anomaly, e));
      opened++;
    }
  }
  // Close what repairs resolved (batch order: after the inserts/updates above).
  let closedByRepair = 0;
  for (const asset of cfg.target_assets) {
    const r = lastRepair(asset);
    if (r === null) continue;
    const n = await env.DB.prepare("SELECT COUNT(*) AS n FROM alerts WHERE asset_id = ? AND status = 'open' AND first_flag_ts < ?").bind(asset, iso(r)).first<{ n: number }>();
    closedByRepair += n?.n ?? 0;
    writes.push(env.DB.prepare("UPDATE alerts SET status = 'resolved' WHERE asset_id = ? AND status = 'open' AND first_flag_ts < ?").bind(asset, iso(r)));
    writes.push(env.DB.prepare("UPDATE maintenance_log SET status = 'done' WHERE asset_id = ? AND kind = 'workorder' AND source = 'scoring' AND status = 'open' AND ts < ?").bind(asset, iso(r)));
  }
  const anomalySummary = {
    artifact: anomalyArt?.run_id ?? null, flags: flags.length, episodes: eps.length,
    alerts_opened: opened, alerts_extended: extended, recorded_resolved: resolved, closed_by_repair: closedByRepair,
  };
  out.anomaly = anomalySummary;
  writes.push(runRow(env, runIds.anomaly, "anomaly", asOf, anomalyArt?.run_id ?? null, started, { ...anomalySummary, config: cfg }));

  // ---- predict
  if (!predictArt) {
    out.predict = { skipped: "no predict artefact in D1" };
  } else {
    const art = predictModel(predictArt);
    const k = Math.floor((asOfMs - firstMs) / HOUR_MS) - (new Date(asOfMs).getUTCHours());
    const featuresAtMs = firstMs + k * HOUR_MS;
    const featuresAt = iso(featuresAtMs);
    const dataStartMs = ms(env.DATA_START ?? "2024-01-01T00:00:00");
    const actions = env.PREDICT_ACTIONS === "on";
    const p: Record<string, number> = {};
    let alerts = 0, workorders = 0;
    const persistence = Math.max(1, Number(art.config?.persistence ?? 1));
    const reset = art.config?.reset_at_repairs === true;
    const featuresAtDay = (asset: string, kk: number) =>
      assetFeatures(grid, asset, kk, hoursSinceRepair(asset, firstMs + kk * HOUR_MS, repairs, dataStartMs), reset ? segmentStart(grid, asset, kk, repairs) : 0);
    const predictAssets = art.config?.target_assets ?? PREDICT_ASSETS;
    for (const asset of predictAssets.filter((a) => cfg.target_assets.includes(a))) {
      const f = featuresAtDay(asset, k);
      const prob = predictProba(art, f);
      const d = drivers(art, f);
      // Alert rule of the artefact: `persistence` consecutive daily scores at or above threshold.
      let alert = prob >= art.threshold;
      for (let j = 1; alert && j < persistence; j++) {
        const kk = k - 24 * j;
        alert = kk >= 0 && predictProba(art, featuresAtDay(asset, kk)) >= art.threshold;
      }
      const text = alert ? interpretDrivers(d) : `below threshold; weak signal ${interpretDrivers(d)}`;
      p[asset] = prob;
      writes.push(env.DB.prepare("INSERT OR REPLACE INTO predictions(run_id, asset_id, as_of, p_fail, horizon_days, drivers) VALUES (?, ?, ?, ?, ?, ?)")
        .bind(runIds.predict, asset, featuresAt, prob, art.horizon_days, JSON.stringify({ drivers: d, interpretation: text, alert, threshold: art.threshold, artifact: art.run_id })));
      if (!alert || !actions) continue;
      const open = await env.DB.prepare("SELECT id FROM alerts WHERE asset_id = ? AND kind = 'predict' AND status = 'open' ORDER BY last_flag_ts DESC LIMIT 1")
        .bind(asset).first<{ id: number }>();
      if (open) {
        writes.push(env.DB.prepare("UPDATE alerts SET last_flag_ts = ?, severity = MAX(severity, ?), run_id = ? WHERE id = ?").bind(featuresAt, prob, runIds.predict, open.id));
      } else {
        writes.push(env.DB.prepare("INSERT INTO alerts(run_id, asset_id, tag, kind, first_flag_ts, last_flag_ts, severity, interpretation) VALUES (?, ?, NULL, 'predict', ?, ?, ?, ?)")
          .bind(runIds.predict, asset, featuresAt, featuresAt, prob, text));
        alerts++;
      }
      const wo = await env.DB.prepare("SELECT id FROM maintenance_log WHERE asset_id = ? AND kind = 'workorder' AND source = 'scoring' AND status = 'open' LIMIT 1").bind(asset).first();
      if (!wo) {
        const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM maintenance_log WHERE kind = 'workorder'").first<{ n: number }>();
        const woId = `WO-${String((count?.n ?? 0) + 1 + workorders).padStart(5, "0")}`;
        const desc = `P(failure within ${art.horizon_days} d) = ${prob.toFixed(2)} >= ${art.threshold}; ${text}. Raised by scoring ${runIds.predict}.`;
        writes.push(env.DB.prepare("INSERT INTO maintenance_log(kind, wo_id, asset_id, ts, type, description, status, source) VALUES ('workorder', ?, ?, ?, 'inspection', ?, 'open', 'scoring')")
          .bind(woId, asset, asOf, desc));
        workorders++;
      }
    }
    const predictSummary = { artifact: art.run_id, features_at: featuresAt, p_fail: p, alerts, workorders };
    out.predict = predictSummary;
    writes.push(runRow(env, runIds.predict, "predict", asOf, art.run_id, started, { ...predictSummary, actions }));
  }

  await env.DB.batch(writes);
  out.rows_read = rowsRead;
  return out;
}

function insertAlert(env: Env, runId: string, e: Flag, status = "open"): D1PreparedStatement {
  return env.DB.prepare("INSERT INTO alerts(run_id, asset_id, tag, kind, first_flag_ts, last_flag_ts, severity, interpretation, status) VALUES (?, ?, ?, 'anomaly', ?, ?, ?, ?, ?)")
    .bind(runId, e.asset, e.tag, e.first_flag_ts, e.last_flag_ts, e.severity, e.interpretation, status);
}

function runRow(env: Env, runId: string, task: string, asOf: string, art: string | null, started: string, summary: unknown): D1PreparedStatement {
  return env.DB.prepare("INSERT OR REPLACE INTO runs(run_id, task, as_of, artifact, started, finished, summary) VALUES (?, ?, ?, ?, ?, ?, ?)")
    .bind(runId, task, asOf, art, started, new Date().toISOString(), JSON.stringify(summary));
}

/** Health must answer even when D1 does not (e.g. the free-tier daily limit): 503 + reason, not error 1101. */
export async function safeHealth(service: string, check: () => Promise<Response>): Promise<Response> {
  try {
    return await check();
  } catch (e) {
    const error = String((e as Error).message ?? e);
    return Response.json(
      { service, status: "degraded", d1_limit_exceeded: /daily row (read|write) limit/i.test(error), error: error.slice(0, 300) },
      { status: 503 },
    );
  }
}

async function health(env: Env): Promise<Response> {
  // Open endpoint: one indexed row.
  const last = await env.DB.prepare("SELECT run_id, as_of, finished FROM runs ORDER BY started DESC LIMIT 1").first<{ run_id: string; as_of: string; finished: string }>();
  return Response.json({ service: "plant-scoring", last_run: last ?? null, plant_api: env.PLANT_API_URL });
}

export default {
  async scheduled(_c: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(
      scoreOnce(env).then(
        (r) => console.log(JSON.stringify({ score: r })),
        (e) => console.error("scoring failed:", String(e)),
      ),
    );
  },

  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    if (url.pathname === "/health" && req.method === "GET") return safeHealth("plant-scoring", () => health(env));
    if (url.pathname === "/score" && req.method === "POST") {
      if (!env.ADMIN_TOKEN || req.headers.get("authorization") !== `Bearer ${env.ADMIN_TOKEN}`)
        return Response.json({ detail: "ADMIN token required" }, { status: 403 });
      try {
        return Response.json(await scoreOnce(env, { force: url.searchParams.get("force") === "1" }));
      } catch (e) {
        return Response.json({ detail: "scoring failed", error: String((e as Error).message ?? e) }, { status: 502 });
      }
    }
    return Response.json({ detail: "Not Found" }, { status: 404 });
  },
};
