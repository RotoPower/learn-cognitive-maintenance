/**
 * Dashboard Worker (module D3): the public management view, no login.
 *
 * Static page in public/ (served by the assets binding); this Worker answers /api/* only.
 * The browser never sees a token: the Worker calls the plant API and the scoring Worker
 * over Service Bindings with the READ / ADMIN tokens it holds as secrets. Only an
 * allow-list of routes is exposed; ground truth is never proxied.
 *
 *   GET  /api/overview          sim time, KPIs, fleet board, alerts, work orders
 *   GET  /api/asset/:id         30-day trends (from the plant API, not D1), alerts,
 *                               prediction, recommended actions, work orders
 *   POST /api/workorder         {asset_id, type, description, confirm: true} -> source='demo'
 *   POST /api/demo/:action      jump7 | jump {day} | score | inject {asset_id} | reset
 *   POST /api/chat              {message, session_id} -> the assistant's NDJSON stream, passed through
 *   POST /api/chat/confirm      {session_id, draft_id} -> the operator's Confirm of a drafted work order
 *
 * Abuse control instead of accounts: the API_LIMITER binding (60 requests/min per
 * viewer), and per-viewer / global hourly caps on demo actions and work orders kept in
 * D1 `demo_actions` (viewer = sha-256 prefix of the IP). Clock moves only forward
 * inside the horizon; reset restores the scripted plant.
 *
 * D1 budget: overview and asset reads are index-bounded and cached per isolate for
 * CACHE_SECONDS; trends come from the plant API, which computes them from the
 * simulator, so opening an asset costs no D1 reads.
 */

export interface Env {
  DB: D1Database;
  ASSETS?: Fetcher;
  PLANT?: { fetch(input: string | Request, init?: RequestInit): Promise<Response> };
  SCORING?: { fetch(input: string | Request, init?: RequestInit): Promise<Response> };
  API_LIMITER?: { limit(o: { key: string }): Promise<{ success: boolean }> };
  PLANT_API_URL: string;
  SCORING_URL: string;
  READ_TOKEN?: string;
  ADMIN_TOKEN?: string;
  SCORING_ADMIN_TOKEN?: string;
  /** Part E assistant behind a Cloudflare Tunnel; unset until the VM exists. ASSISTANT is a
   *  test-only binding standing in for it. */
  ASSISTANT_URL?: string;
  ASSISTANT_SECRET?: string;
  ASSISTANT?: { fetch(input: string | Request, init?: RequestInit): Promise<Response> };
  /** "on" once the predict model has a validator GO: risk then drives asset status. */
  PREDICT_TRUSTED?: string;
  CACHE_SECONDS?: string;
  DEMO_PER_VIEWER_HOUR?: string;
  DEMO_GLOBAL_HOUR?: string;
  SCORE_GLOBAL_DAY?: string;
  WORKORDER_PER_VIEWER_HOUR?: string;
}

const UA = "plant-dashboard/0.1 (+https://github.com/RotoPower/learn-cognitive-maintenance)";
const DAY_MS = 86_400_000;
const HORIZON_START = Date.parse("2024-01-01T00:00:00Z");

class HttpError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

const iso = (ms: number) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "");
const ms = (s: string) => Date.parse(/Z$/.test(s) ? s : `${s}Z`);

// ------------------------------------------------------------------ upstreams

async function upstream<T>(env: Env, which: "plant" | "scoring", path: string, init: { method?: string; body?: unknown; admin?: boolean } = {}): Promise<T> {
  const base = which === "plant" ? env.PLANT_API_URL : env.SCORING_URL;
  const binding = which === "plant" ? env.PLANT : env.SCORING;
  const token = which === "scoring" ? (env.SCORING_ADMIN_TOKEN ?? env.ADMIN_TOKEN) : init.admin ? env.ADMIN_TOKEN : env.READ_TOKEN;
  const url = `${base.replace(/\/+$/, "")}${path}`;
  const req: RequestInit = {
    method: init.method ?? "GET",
    headers: { authorization: `Bearer ${token ?? ""}`, "user-agent": UA, accept: "application/json", ...(init.body ? { "content-type": "application/json" } : {}) },
    body: init.body ? JSON.stringify(init.body) : undefined,
  };
  const r = binding ? await binding.fetch(url, req) : await fetch(url, req);
  const text = await r.text();
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!r.ok) {
    // Pass the upstream's reason (e.g. "overlaps an existing scenario"), never its headers or tokens.
    const detail = (data as { detail?: string } | null)?.detail ?? `upstream ${r.status}`;
    throw new HttpError(r.status >= 500 ? 502 : r.status, `${which}: ${detail}`);
  }
  return data as T;
}

interface Clock { sim_time: string; speed: number; horizon_end: string }
interface Asset { asset_id: string; description: string; tags: string[] }
interface LogEntry { kind: string; asset_id: string; timestamp: string; id?: string; type?: string; description?: string; status?: string }

// ------------------------------------------------------------------ cache

const cache = new Map<string, { at: number; value: unknown }>();
async function cached<T>(env: Env, key: string, fn: () => Promise<T>): Promise<T> {
  const ttl = Number(env.CACHE_SECONDS ?? 30) * 1000;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < ttl) return hit.value as T;
  const value = await fn();
  cache.set(key, { at: Date.now(), value });
  return value;
}
export function clearCache(): void { cache.clear(); }

// ------------------------------------------------------------------ status

const ASSET_MODES: Record<string, string> = { GT1: "compressor_fouling", BFP1: "bearing_wear", BFP2: "bearing_wear", CTF1: "gearbox_wear" };

/** Interim actions until the Part E playbook fills D1 `playbook`. */
const FALLBACK_ACTIONS: Record<string, string[]> = {
  bearing_wear: [
    "Take a vibration spectrum on the drive-end bearing and compare with the last baseline.",
    "Check lubrication: grease quantity, interval, and signs of contamination.",
    "Plan a bearing replacement window if DE vibration keeps rising; stage spares.",
  ],
  compressor_fouling: [
    "Compare compressor discharge pressure and exhaust temperature at the same load.",
    "Schedule an online compressor wash; plan an offline wash at the next outage.",
    "Inspect the inlet filters and their differential pressure.",
  ],
  gearbox_wear: [
    "Sample the gearbox oil for wear metals and check the oil temperature trend.",
    "Inspect the gear mesh and the oil cooler; check the oil level.",
    "Plan a gearbox inspection; stage bearings and seals.",
  ],
};

interface AlertRow { id: number; asset_id: string; tag: string | null; kind: string; first_flag_ts: string; last_flag_ts: string; severity: number; interpretation: string | null; status: string }
interface PredictionRow { asset_id: string; as_of: string; p_fail: number; horizon_days: number; drivers: string }

type Status = "healthy" | "warning" | "critical";

function assetStatus(asset: string, alerts: AlertRow[], pred: { p: number; threshold: number } | null, now: number, trusted: boolean): Status {
  const active = alerts.filter((a) => a.asset_id === asset && a.kind === "anomaly" && a.status === "open" && ms(a.last_flag_ts) >= now - 7 * DAY_MS);
  if (active.some((a) => a.severity >= 6) || (trusted && pred && pred.p >= pred.threshold)) return "critical";
  if (active.length > 0 || (trusted && pred && pred.p >= pred.threshold / 2)) return "warning";
  return "healthy";
}

// ------------------------------------------------------------------ reads

async function latestPredictions(env: Env, simNow: string): Promise<{ run_id: string | null; rows: PredictionRow[] }> {
  const run = await env.DB.prepare("SELECT run_id FROM runs WHERE task = 'predict' AND as_of <= ? ORDER BY as_of DESC LIMIT 1").bind(simNow).first<{ run_id: string }>();
  if (!run) return { run_id: null, rows: [] };
  const rows = await env.DB.prepare("SELECT asset_id, as_of, p_fail, horizon_days, drivers FROM predictions WHERE run_id = ?").bind(run.run_id).all<PredictionRow>();
  return { run_id: run.run_id, rows: rows.results };
}

function parseDrivers(p: PredictionRow) {
  const d = JSON.parse(p.drivers || "{}") as { drivers?: [string, number][]; interpretation?: string; threshold?: number; artifact?: string };
  return { drivers: d.drivers ?? [], interpretation: d.interpretation ?? "", threshold: d.threshold ?? 0.5, artifact: d.artifact ?? null };
}

export async function overview(env: Env) {
  return cached(env, "overview", async () => {
    const [clock, assets, log] = await Promise.all([
      upstream<Clock>(env, "plant", "/clock"),
      upstream<Asset[]>(env, "plant", "/assets"),
      upstream<LogEntry[]>(env, "plant", "/maintenance/log"),
    ]);
    const simNow = iso(Math.floor(ms(clock.sim_time) / 3_600_000) * 3_600_000);
    const now = ms(simNow);
    const trusted = env.PREDICT_TRUSTED === "on";
    const alerts = (await env.DB.prepare(
      "SELECT id, asset_id, tag, kind, first_flag_ts, last_flag_ts, severity, interpretation, status FROM alerts WHERE first_flag_ts <= ? ORDER BY last_flag_ts DESC LIMIT 100",
    ).bind(simNow).all<AlertRow>()).results;
    const preds = await latestPredictions(env, simNow);
    const workorders = log.filter((e) => e.kind === "workorder" && ms(e.timestamp) <= now).reverse();
    const lastRun = await env.DB.prepare("SELECT as_of FROM runs WHERE task = 'anomaly' AND as_of <= ? ORDER BY as_of DESC LIMIT 1").bind(simNow).first<{ as_of: string }>();

    const board = assets.filter((a) => a.asset_id in ASSET_MODES).map((a) => {
      const pr = preds.rows.find((r) => r.asset_id === a.asset_id);
      const d = pr ? parseDrivers(pr) : null;
      const status = assetStatus(a.asset_id, alerts, pr && d ? { p: pr.p_fail, threshold: d.threshold } : null, now, trusted);
      const top = alerts
        .filter((x) => x.asset_id === a.asset_id && x.kind === "anomaly" && x.status === "open" && ms(x.last_flag_ts) >= now - 7 * DAY_MS)
        .sort((x, y) => y.severity - x.severity)[0];
      const repairs = log.filter((e) => e.kind === "corrective_repair" && e.asset_id === a.asset_id && ms(e.timestamp) <= now);
      const lastMaint = repairs.length ? repairs[repairs.length - 1].timestamp : null;
      return {
        asset_id: a.asset_id,
        description: a.description,
        status,
        risk: pr ? pr.p_fail : null,
        risk_alert: pr && d ? pr.p_fail >= d.threshold : false,
        // A model driver is shown only for an asset above the risk threshold: below it the
        // top contribution is noise (often hours_since_repair, a known clock proxy).
        top_driver: top?.tag ?? (pr && d && pr.p_fail >= d.threshold ? d.drivers[0]?.[0] ?? null : null),
        top_driver_source: top ? "anomaly" : pr && d && pr.p_fail >= d.threshold && d.drivers.length ? "risk model" : null,
        days_since_maintenance: lastMaint ? Math.floor((now - ms(lastMaint)) / DAY_MS) : null,
        open_alerts: alerts.filter((x) => x.asset_id === a.asset_id && x.status === "open").length,
      };
    });
    const threshold = preds.rows.length ? parseDrivers(preds.rows[0]).threshold : null;
    return {
      sim_time: simNow,
      speed: clock.speed,
      horizon_end: clock.horizon_end,
      last_scored: lastRun?.as_of ?? null,
      predict: { trusted, threshold, run_id: preds.run_id },
      kpis: {
        healthy: board.filter((b) => b.status === "healthy").length,
        warning: board.filter((b) => b.status === "warning").length,
        critical: board.filter((b) => b.status === "critical").length,
        open_workorders: workorders.filter((w) => w.status === "open").length,
        predicted_failures_30d: board.filter((b) => b.risk_alert).length,
        alerts_7d: alerts.filter((x) => ms(x.last_flag_ts) >= now - 7 * DAY_MS).length,
      },
      assets: board,
      alerts: alerts.slice(0, 50),
      workorders: workorders.slice(0, 50).map((w) => ({ id: w.id, asset_id: w.asset_id, type: w.type, description: w.description, timestamp: w.timestamp, status: w.status })),
    };
  });
}

export async function assetDetail(env: Env, assetId: string) {
  if (!(assetId in ASSET_MODES)) throw new HttpError(404, "unknown asset");
  return cached(env, `asset:${assetId}`, async () => {
    const [clock, assets, log] = await Promise.all([
      upstream<Clock>(env, "plant", "/clock"),
      upstream<Asset[]>(env, "plant", "/assets"),
      upstream<LogEntry[]>(env, "plant", `/maintenance/log?asset_id=${assetId}`),
    ]);
    const simNow = iso(Math.floor(ms(clock.sim_time) / 3_600_000) * 3_600_000);
    const now = ms(simNow);
    const from = iso(Math.max(now - 30 * DAY_MS, HORIZON_START));
    const tags = assets.find((a) => a.asset_id === assetId)?.tags ?? [];
    const trends = await Promise.all(tags.map(async (tag) => {
      const h = await upstream<{ points: { timestamp: string; value: number | null }[] }>(env, "plant", `/tags/${encodeURIComponent(tag)}/history?from=${from}&to=${simNow}&interval=3h`);
      const vals = h.points.map((p) => p.value).filter((v): v is number => v !== null);
      const dead = vals.length > 1 && vals.every((v) => v === vals[0]);
      return { tag, dead, points: h.points.map((p) => [p.timestamp, p.value] as [string, number | null]) };
    }));
    const alerts = (await env.DB.prepare(
      "SELECT id, asset_id, tag, kind, first_flag_ts, last_flag_ts, severity, interpretation, status FROM alerts WHERE asset_id = ? AND first_flag_ts <= ? AND last_flag_ts >= ? ORDER BY last_flag_ts DESC LIMIT 50",
    ).bind(assetId, simNow, from).all<AlertRow>()).results;
    const preds = await latestPredictions(env, simNow);
    const pr = preds.rows.find((r) => r.asset_id === assetId);
    const d = pr ? parseDrivers(pr) : null;
    const mode = ASSET_MODES[assetId];
    const playbook = await env.DB.prepare("SELECT body FROM playbook WHERE mode = ? AND section = 'actions'").bind(mode).first<{ body: string }>();
    return {
      asset_id: assetId,
      sim_time: simNow,
      mode,
      trends,
      alerts,
      prediction: pr && d ? { p_fail: pr.p_fail, horizon_days: pr.horizon_days, as_of: pr.as_of, threshold: d.threshold, drivers: d.drivers.slice(0, 3), interpretation: d.interpretation, trusted: env.PREDICT_TRUSTED === "on" } : null,
      actions: playbook ? { source: "playbook", items: playbook.body.split(/\n+/).map((s) => s.replace(/^[-*]\s*/, "")).filter(Boolean) } : { source: "interim", items: FALLBACK_ACTIONS[mode] },
      workorders: log.filter((e) => e.kind === "workorder" && ms(e.timestamp) <= now).reverse().slice(0, 20),
      repairs: log.filter((e) => e.kind === "corrective_repair" && ms(e.timestamp) <= now).map((e) => e.timestamp),
    };
  });
}

// ------------------------------------------------------------------ writes (rate limited)

async function viewerId(req: Request): Promise<string> {
  const ip = req.headers.get("cf-connecting-ip") ?? "local";
  const h = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`plant-dashboard:${ip}`));
  return [...new Uint8Array(h)].slice(0, 8).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function spend(env: Env, viewer: string, kind: string): Promise<void> {
  const hourAgo = new Date(Date.now() - 3_600_000).toISOString();
  const dayAgo = new Date(Date.now() - DAY_MS).toISOString();
  const isWo = kind === "workorder";
  const perViewer = Number(isWo ? env.WORKORDER_PER_VIEWER_HOUR ?? 10 : env.DEMO_PER_VIEWER_HOUR ?? 5);
  const mine = await env.DB.prepare(`SELECT COUNT(*) AS n FROM demo_actions WHERE viewer = ? AND ts >= ? AND ${isWo ? "kind = 'workorder'" : "kind <> 'workorder'"}`)
    .bind(viewer, hourAgo).first<{ n: number }>();
  if ((mine?.n ?? 0) >= perViewer) throw new HttpError(429, `limit reached: ${perViewer} ${isWo ? "work orders" : "demo actions"} per hour per viewer`);
  if (!isWo) {
    const all = await env.DB.prepare("SELECT COUNT(*) AS n FROM demo_actions WHERE kind <> 'workorder' AND ts >= ?").bind(hourAgo).first<{ n: number }>();
    const cap = Number(env.DEMO_GLOBAL_HOUR ?? 60);
    if ((all?.n ?? 0) >= cap) throw new HttpError(429, `the demo is busy: ${cap} actions per hour across all viewers`);
  }
  if (kind === "score") {
    const s = await env.DB.prepare("SELECT COUNT(*) AS n FROM demo_actions WHERE kind = 'score' AND ts >= ?").bind(dayAgo).first<{ n: number }>();
    const cap = Number(env.SCORE_GLOBAL_DAY ?? 30);
    if ((s?.n ?? 0) >= cap) throw new HttpError(429, `scoring runs are capped at ${cap} per day (database budget); the hourly schedule still runs`);
  }
  await env.DB.prepare("INSERT INTO demo_actions(kind, viewer, ts) VALUES (?, ?, ?)").bind(kind, viewer, new Date().toISOString()).run();
}

async function body<T>(req: Request): Promise<T> {
  try { return (await req.json()) as T; } catch { throw new HttpError(422, "JSON body required"); }
}

export async function createWorkorder(env: Env, req: Request) {
  const b = await body<{ asset_id?: string; type?: string; description?: string; confirm?: boolean }>(req);
  if (!b.asset_id || !(b.asset_id in ASSET_MODES)) throw new HttpError(422, "asset_id must be one of GT1, BFP1, BFP2, CTF1");
  const type = b.type ?? "inspection";
  if (!["inspection", "repair", "replacement", "lubrication", "other"].includes(type)) throw new HttpError(422, "bad type");
  const description = (b.description ?? "").trim().slice(0, 500);
  if (b.confirm !== true) return { draft: { asset_id: b.asset_id, type, description }, confirm_required: true };
  await spend(env, await viewerId(req), "workorder");
  const clock = await upstream<Clock>(env, "plant", "/clock");
  const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM maintenance_log WHERE kind = 'workorder'").first<{ n: number }>();
  const woId = `WO-${String((count?.n ?? 0) + 1).padStart(5, "0")}`;
  const ts = iso(Math.floor(ms(clock.sim_time) / 1000) * 1000);
  await env.DB.prepare("INSERT INTO maintenance_log(kind, wo_id, asset_id, ts, type, description, status, source) VALUES ('workorder', ?, ?, ?, ?, ?, 'open', 'demo')")
    .bind(woId, b.asset_id, ts, type, description).run();
  clearCache();
  return { id: woId, asset_id: b.asset_id, type, description, timestamp: ts, status: "open", source: "demo" };
}

// ------------------------------------------------------------------ assistant proxy (Part E)

/** Forward to the assistant with the shared secret and the viewer's IP (for its per-viewer limit).
 *  The browser never sees the secret or the assistant's address. */
export async function assistant(env: Env, req: Request, path: "/chat" | "/workorders/confirm"): Promise<Response> {
  if (!env.ASSISTANT && !env.ASSISTANT_URL) throw new HttpError(503, "the assistant is not connected yet (Part E); the rest of the dashboard works");
  const body = await req.text();
  if (body.length > 4000) throw new HttpError(413, "message too long");
  const init: RequestInit = {
    method: "POST",
    body,
    headers: {
      "content-type": "application/json",
      "x-demo-secret": env.ASSISTANT_SECRET ?? "",
      "x-viewer-ip": req.headers.get("cf-connecting-ip") ?? "unknown",
      "user-agent": UA,
    },
  };
  const url = `${(env.ASSISTANT_URL ?? "http://assistant").replace(/\/+$/, "")}${path}`;
  let r: Response;
  try {
    r = env.ASSISTANT ? await env.ASSISTANT.fetch(url, init) : await fetch(url, init);
  } catch {
    throw new HttpError(502, "the assistant is not reachable right now");
  }
  if (!r.ok) {
    const detail = ((await r.json().catch(() => null)) as { detail?: string } | null)?.detail;
    // 401 means the Worker's secret is wrong: an operator problem, not the viewer's
    throw new HttpError(r.status === 401 ? 502 : r.status, r.status === 401 ? "the assistant rejected the dashboard (secret mismatch)" : detail ?? `assistant error ${r.status}`);
  }
  if (path === "/workorders/confirm") clearCache();
  return new Response(r.body, { status: 200, headers: { "content-type": r.headers.get("content-type") ?? "application/json", "cache-control": "no-store" } });
}

export async function demo(env: Env, req: Request, action: string) {
  const viewer = await viewerId(req);
  const clock = await upstream<Clock>(env, "plant", "/clock");
  const now = ms(clock.sim_time), end = ms(clock.horizon_end);
  let result: unknown;
  if (action === "jump7" || action === "jump") {
    let target = now + 7 * DAY_MS;
    if (action === "jump") {
      const { day } = await body<{ day?: number }>(req);
      if (typeof day !== "number" || !Number.isInteger(day) || day < 1 || day > 366) throw new HttpError(422, "day must be an integer in 1..366");
      target = HORIZON_START + (day - 1) * DAY_MS;
    }
    if (target <= now) throw new HttpError(422, "the clock only moves forward; use Reset to start over");
    target = Math.min(target, end);
    if (target <= now) throw new HttpError(422, "already at the end of the simulated year; use Reset");
    await spend(env, viewer, "jump");
    result = await upstream(env, "plant", "/clock/jump", { method: "POST", body: { to: iso(target) }, admin: true });
  } else if (action === "score") {
    await spend(env, viewer, "score");
    result = await upstream(env, "scoring", "/score", { method: "POST" });
  } else if (action === "inject") {
    const { asset_id } = await body<{ asset_id?: string }>(req);
    if (!asset_id || !(asset_id in ASSET_MODES)) throw new HttpError(422, "asset_id must be one of GT1, BFP1, BFP2, CTF1");
    await spend(env, viewer, "inject");
    result = await upstream(env, "plant", "/admin/inject_fault", {
      method: "POST", admin: true,
      body: { asset: asset_id, mode: ASSET_MODES[asset_id], onset: iso(now + 3_600_000), duration_days: 14 },
    });
  } else if (action === "reset") {
    await spend(env, viewer, "reset");
    result = await upstream(env, "plant", "/admin/reset", { method: "POST", body: { seed: 42 }, admin: true });
    // Scoring outputs belong to the old timeline. Readings stay: they are the same for the scripted plant.
    await env.DB.batch([
      env.DB.prepare("DELETE FROM alerts"), env.DB.prepare("DELETE FROM predictions"), env.DB.prepare("DELETE FROM runs"),
    ]);
  } else {
    throw new HttpError(404, "unknown demo action");
  }
  clearCache();
  return { action, result };
}

// ------------------------------------------------------------------ router

function json(data: unknown, status = 200): Response {
  return Response.json(data, { status, headers: { "cache-control": "no-store" } });
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    if (!url.pathname.startsWith("/api/")) {
      return env.ASSETS ? env.ASSETS.fetch(req) : new Response("Not Found", { status: 404 });
    }
    try {
      if (env.API_LIMITER) {
        const { success } = await env.API_LIMITER.limit({ key: await viewerId(req) });
        if (!success) throw new HttpError(429, "too many requests; slow down");
      }
      const p = url.pathname;
      if (p === "/api/overview" && req.method === "GET") return json(await overview(env));
      const a = /^\/api\/asset\/([A-Z0-9]+)$/.exec(p);
      if (a && req.method === "GET") return json(await assetDetail(env, a[1]));
      if (p === "/api/workorder" && req.method === "POST") return json(await createWorkorder(env, req));
      if (p === "/api/chat" && req.method === "POST") return await assistant(env, req, "/chat");
      if (p === "/api/chat/confirm" && req.method === "POST") return await assistant(env, req, "/workorders/confirm");
      const d = /^\/api\/demo\/([a-z0-9]+)$/.exec(p);
      if (d && req.method === "POST") return json(await demo(env, req, d[1]));
      throw new HttpError(404, "Not Found");
    } catch (e) {
      if (e instanceof HttpError) return json({ detail: e.message }, e.status);
      const msg = String((e as Error).message ?? e);
      const limit = /daily row (read|write) limit/i.test(msg);
      return json({ detail: limit ? "the demo database hit its free daily limit; it resets at 00:00 UTC" : "internal error" }, 503);
    }
  },
} satisfies ExportedHandler<Env>;
