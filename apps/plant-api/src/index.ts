/**
 * Plant API Worker: same routes and semantics as plant/api.py.
 *
 *   GET  /clock                     READ      POST /clock/speed, /clock/jump   ADMIN
 *   GET  /assets                    READ
 *   GET  /tags/latest?asset_id=     READ
 *   GET  /tags/{tag}/history?from=&to=&interval=1h     READ (to is clamped to sim now)
 *   GET  /maintenance/log?asset_id= READ      POST /maintenance/workorder      READ
 *   GET  /admin/ground_truth        ADMIN     POST /admin/inject_fault, /admin/reset
 *   GET  /admin/model_artifacts     ADMIN     POST /admin/model_artifacts
 *
 * State: the Clock Durable Object (clock + scenario overlay) and D1 (work orders,
 * artefacts, ground-truth mirror, assets). Readings are never stored here: they
 * are recomputed from (seed, asset, tag, sim_time) on every request.
 */
import { DEFAULT_FAULTS } from "./faults";
import { ASSETS, ASSET_INFO, FAULT_MODES, PLANT_LOAD_TAG, Plant, TAGS, canonicalAsset, isoNaive, parseNaiveIso, splitTag } from "./sim";
import type { PlantConfig, ScenarioItem } from "./sim";
import { Clock } from "./clock";

export { Clock };

export interface Env {
  DB: D1Database;
  CLOCK: DurableObjectNamespace<Clock>;
  READ_TOKEN?: string;
  ADMIN_TOKEN?: string;
  PLANT_CLOCK_START?: string;
  PLANT_CLOCK_SPEED?: string;
}

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body, (_k, v) => (typeof v === "number" && Number.isNaN(v) ? null : v)), {
    status,
    headers: { "content-type": "application/json" },
  });

// ----------------------------------------------------------------------------- auth

function token(req: Request): string | null {
  const auth = req.headers.get("authorization") ?? "";
  if (auth.toLowerCase().startsWith("bearer ")) return auth.slice(7).trim();
  return req.headers.get("x-api-key");
}

function requireRead(req: Request, env: Env): void {
  const t = token(req);
  const read = env.READ_TOKEN ?? "read-token";
  const admin = env.ADMIN_TOKEN ?? "admin-token";
  if (t !== read && t !== admin) throw new HttpError(401, "READ or ADMIN token required");
}

function requireAdmin(req: Request, env: Env): void {
  if (token(req) !== (env.ADMIN_TOKEN ?? "admin-token")) throw new HttpError(403, "ADMIN token required");
}

// ---------------------------------------------------------------------------- helpers

function clockStub(env: Env) {
  return env.CLOCK.get(env.CLOCK.idFromName("plant"));
}

async function currentPlant(env: Env): Promise<{ plant: Plant; now: Date; speed: number }> {
  const clock = clockStub(env);
  const [script, { simMs, speed }] = await Promise.all([clock.script(), clock.now()]);
  const cfg: PlantConfig = { ...DEFAULT_FAULTS, seed: script.seed, scenarios: [...DEFAULT_FAULTS.scenarios, ...script.injected] };
  return { plant: new Plant(cfg), now: new Date(simMs), speed };
}

function parseInterval(text: string): number {
  const m = /^(\d+)([mhd])$/.exec(text.trim().toLowerCase());
  if (!m) throw new HttpError(422, "interval must look like 15m, 1h or 1d");
  const n = Number(m[1]);
  const hours = n * ({ m: 1 / 60, h: 1, d: 24 } as Record<string, number>)[m[2]];
  if (hours <= 0) throw new HttpError(422, "interval must be positive");
  return hours;
}

function parseTs(s: string | null, name: string): Date {
  if (!s) throw new HttpError(422, `${name} is required`);
  try {
    return parseNaiveIso(s);
  } catch {
    throw new HttpError(422, `${name}: bad timestamp '${s}'`);
  }
}

function resolveAsset(id: string): string {
  try {
    return canonicalAsset(id);
  } catch {
    throw new HttpError(404, `unknown asset '${id}'`);
  }
}

async function body<T>(req: Request): Promise<T> {
  try {
    return (await req.json()) as T;
  } catch {
    throw new HttpError(422, "body must be JSON");
  }
}

/** Mirror the current script's ground truth into D1 (admin/validator reads, other Workers join on it). */
async function writeGroundTruth(env: Env, plant: Plant): Promise<void> {
  const stmts = [env.DB.prepare("DELETE FROM ground_truth")];
  for (const f of plant.failures())
    stmts.push(env.DB.prepare("INSERT INTO ground_truth(kind, asset_id, mode, onset, failure) VALUES ('failure', ?, ?, ?, ?)").bind(f.asset, f.mode, f.onset, f.failure));
  for (const e of plant.events())
    stmts.push(env.DB.prepare("INSERT INTO ground_truth(kind, asset_id, mode, from_ts, to_ts) VALUES ('event', ?, ?, ?, ?)").bind(e.asset, e.event, e.from, e.to));
  await env.DB.batch(stmts);
}

async function seedAssets(env: Env): Promise<void> {
  await env.DB.batch(
    ASSETS.map((a) =>
      env.DB.prepare("INSERT OR REPLACE INTO assets(asset_id, description, tags) VALUES (?, ?, ?)").bind(
        a,
        ASSET_INFO[a],
        JSON.stringify(Object.keys(TAGS[a]).map((t) => `${a}.${t}`)),
      ),
    ),
  );
}

// ---------------------------------------------------------------------------- routes

async function handle(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const method = req.method.toUpperCase();

  if (path === "/" && method === "GET") {
    return json({
      service: "plant-api (Cloudflare Worker)",
      auth: "Authorization: Bearer <READ|ADMIN token>",
      routes: [
        "GET /clock", "POST /clock/speed", "POST /clock/jump", "GET /assets", "GET /tags/latest", "GET /tags/{tag}/history",
        "GET /maintenance/log", "POST /maintenance/workorder", "GET /admin/ground_truth", "POST /admin/inject_fault",
        "POST /admin/reset", "GET /admin/model_artifacts", "POST /admin/model_artifacts",
      ],
    });
  }

  // ----- clock -----
  if (path === "/clock" && method === "GET") {
    requireRead(req, env);
    const { simMs, speed } = await clockStub(env).now();
    return json({ sim_time: isoNaive(new Date(simMs)), speed });
  }
  if (path === "/clock/speed" && method === "POST") {
    requireAdmin(req, env);
    const b = await body<{ speed?: number }>(req);
    if (typeof b.speed !== "number" || b.speed < 0 || b.speed > 86_400 * 30) throw new HttpError(422, "speed must be a number in [0, 2592000]");
    const r = await clockStub(env).setSpeed(b.speed);
    return json({ sim_time: isoNaive(new Date(r.simMs)), speed: r.speed });
  }
  if (path === "/clock/jump" && method === "POST") {
    requireAdmin(req, env);
    const b = await body<{ to?: string }>(req);
    const to = parseTs(b.to ?? null, "to");
    const { plant } = await currentPlant(env);
    if (to < plant.start || to > plant.end) throw new HttpError(422, `sim_time must be within [${isoNaive(plant.start)}, ${isoNaive(plant.end)}]`);
    const r = await clockStub(env).jump(to.getTime());
    return json({ sim_time: isoNaive(new Date(r.simMs)), speed: r.speed });
  }

  // ----- plant (READ) -----
  if (path === "/assets" && method === "GET") {
    requireRead(req, env);
    return json(ASSETS.map((a) => ({ asset_id: a, description: ASSET_INFO[a], tags: Object.keys(TAGS[a]).map((t) => `${a}.${t}`) })));
  }

  if (path === "/tags/latest" && method === "GET") {
    requireRead(req, env);
    const { plant, now } = await currentPlant(env);
    const t = new Date(Math.floor(now.getTime() / 3_600_000) * 3_600_000); // top of the hour
    const assetId = url.searchParams.get("asset_id");
    const assets = assetId ? [resolveAsset(assetId)] : ASSETS;
    const values: Record<string, number | null> = { [PLANT_LOAD_TAG]: plant.load(t) };
    for (const a of assets) for (const tag of Object.keys(TAGS[a])) {
      const v = plant.value(a, tag, t);
      values[`${a}.${tag}`] = Number.isNaN(v) ? null : v;
    }
    return json({ timestamp: isoNaive(t), values });
  }

  const hist = /^\/tags\/([^/]+)\/history$/.exec(path);
  if (hist && method === "GET") {
    requireRead(req, env);
    const tag = decodeURIComponent(hist[1]);
    const stepH = parseInterval(url.searchParams.get("interval") ?? "1h");
    const maxPoints = Number(url.searchParams.get("max_points") ?? 20_000);
    const from = parseTs(url.searchParams.get("from"), "from");
    let to = parseTs(url.searchParams.get("to"), "to");
    const { plant, now } = await currentPlant(env);
    let fn: (t: Date) => number;
    if (tag === PLANT_LOAD_TAG) fn = (t) => plant.load(t);
    else {
      if (!tag.includes(".")) throw new HttpError(422, "tag must look like ASSET.TAG");
      const [aRaw, name] = splitTag(tag);
      const a = resolveAsset(aRaw);
      if (!TAGS[a][name]) throw new HttpError(404, `unknown tag '${tag}'`);
      fn = (t) => plant.value(a, name, t);
    }
    if (to > now) to = now; // never the future
    if (from > to) return json({ tag, interval: url.searchParams.get("interval") ?? "1h", points: [] });
    const n = Math.floor((to.getTime() - from.getTime()) / 3_600_000 / stepH) + 1;
    if (n > maxPoints) throw new HttpError(422, `${n} points requested; raise max_points or coarsen interval`);
    const points = [];
    for (let i = 0; i < n; i++) {
      const t = new Date(from.getTime() + i * stepH * 3_600_000);
      const v = fn(t);
      points.push({ timestamp: isoNaive(t), value: Number.isNaN(v) ? null : v });
    }
    return json({ tag, interval: url.searchParams.get("interval") ?? "1h", points });
  }

  // ----- maintenance (READ) -----
  if (path === "/maintenance/log" && method === "GET") {
    requireRead(req, env);
    const { plant, now } = await currentPlant(env);
    const assetId = url.searchParams.get("asset_id");
    const asset = assetId ? resolveAsset(assetId) : null;
    const entries: Record<string, unknown>[] = [];
    for (const f of plant.failures()) {
      if (parseNaiveIso(f.repair) <= now && (!asset || f.asset === asset))
        entries.push({ kind: "corrective_repair", asset_id: f.asset, timestamp: f.repair, description: `Failure: ${f.mode.replace(/_/g, " ")}; component replaced` });
    }
    const rows = asset
      ? await env.DB.prepare("SELECT * FROM maintenance_log WHERE kind='workorder' AND asset_id=? ORDER BY ts").bind(asset).all()
      : await env.DB.prepare("SELECT * FROM maintenance_log WHERE kind='workorder' ORDER BY ts").all();
    for (const r of rows.results as Record<string, unknown>[])
      entries.push({ kind: "workorder", id: r.wo_id, asset_id: r.asset_id, type: r.type, description: r.description, timestamp: r.ts, scheduled_for: r.scheduled_for, status: r.status });
    entries.sort((x, y) => String(x.timestamp).localeCompare(String(y.timestamp)));
    return json(entries);
  }

  if (path === "/maintenance/workorder" && method === "POST") {
    requireRead(req, env);
    const b = await body<{ asset_id?: string; type?: string; description?: string; scheduled_for?: string }>(req);
    if (!b.asset_id) throw new HttpError(422, "asset_id is required");
    const asset = resolveAsset(b.asset_id);
    const type = b.type ?? "inspection";
    if (!["inspection", "repair", "replacement", "lubrication", "other"].includes(type)) throw new HttpError(422, "bad type");
    const { now } = await currentPlant(env);
    const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM maintenance_log WHERE kind='workorder'").first<{ n: number }>();
    const woId = `WO-${String((count?.n ?? 0) + 1).padStart(5, "0")}`;
    const wo = {
      kind: "workorder", id: woId, asset_id: asset, type, description: b.description ?? "", timestamp: isoNaive(now),
      scheduled_for: b.scheduled_for ? isoNaive(parseTs(b.scheduled_for, "scheduled_for")) : null, status: "open",
    };
    await env.DB.prepare("INSERT INTO maintenance_log(kind, wo_id, asset_id, ts, type, description, scheduled_for, status) VALUES ('workorder', ?, ?, ?, ?, ?, ?, 'open')")
      .bind(woId, asset, wo.timestamp, type, wo.description, wo.scheduled_for).run();
    return json(wo, 201);
  }

  // ----- admin -----
  if (path === "/admin/ground_truth" && method === "GET") {
    requireAdmin(req, env);
    const { plant, now } = await currentPlant(env);
    const health_now: Record<string, number> = {};
    for (const a of ASSETS) health_now[a] = plant.health(a, now);
    return json({ seed: plant.seed, sim_time: isoNaive(now), failures: plant.failures(), events: plant.events(), health_now });
  }

  if (path === "/admin/inject_fault" && method === "POST") {
    requireAdmin(req, env);
    const b = await body<{ asset?: string; mode?: string; onset?: string; duration_days?: number }>(req);
    if (!b.asset || !b.mode || !b.onset || typeof b.duration_days !== "number" || b.duration_days <= 0) throw new HttpError(422, "asset, mode, onset, duration_days required");
    const asset = resolveAsset(b.asset);
    if (!(b.mode in FAULT_MODES)) throw new HttpError(422, `unknown mode; choose from ${Object.keys(FAULT_MODES).sort().join(", ")}`);
    const { plant } = await currentPlant(env);
    const onsetDay = plant.toHours(parseTs(b.onset, "onset")) / 24;
    const item: ScenarioItem = { asset, mode: b.mode, onset_day: onsetDay, duration_days: b.duration_days };
    const cfg: PlantConfig = { ...DEFAULT_FAULTS, seed: plant.seed, scenarios: [...DEFAULT_FAULTS.scenarios, ...(await clockStub(env).script()).injected, item] };
    let next: Plant;
    try {
      next = new Plant(cfg); // validates overlap
    } catch (e) {
      throw new HttpError(409, (e as Error).message);
    }
    await clockStub(env).inject(item);
    await writeGroundTruth(env, next);
    const onsetH = onsetDay * 24;
    return json({ asset, mode: b.mode, onset: isoNaive(next.toDate(onsetH)), failure: isoNaive(next.toDate(onsetH + b.duration_days * 24)) }, 201);
  }

  if (path === "/admin/reset" && method === "POST") {
    requireAdmin(req, env);
    const b = await body<{ seed?: number }>(req);
    const seed = typeof b.seed === "number" ? Math.trunc(b.seed) : 42;
    const r = await clockStub(env).reset(seed);
    const plant = new Plant({ ...DEFAULT_FAULTS, seed });
    await env.DB.batch([env.DB.prepare("DELETE FROM maintenance_log WHERE kind='workorder'")]);
    await writeGroundTruth(env, plant);
    await seedAssets(env);
    return json({ seed, sim_time: isoNaive(new Date(r.clock.simMs)), speed: r.clock.speed });
  }

  if (path === "/admin/model_artifacts" && method === "GET") {
    requireAdmin(req, env);
    const rows = await env.DB.prepare("SELECT run_id FROM model_artifacts ORDER BY run_id").all<{ run_id: string }>();
    return json(rows.results.map((r) => r.run_id));
  }

  if (path === "/admin/model_artifacts" && method === "POST") {
    requireAdmin(req, env);
    const b = await body<{ run_id?: string; seed?: number; model?: unknown; metrics?: unknown; meta?: unknown; task?: string }>(req);
    if (!b.run_id || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(b.run_id)) throw new HttpError(422, "run_id must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$");
    if (typeof b.seed !== "number") throw new HttpError(422, "seed is required");
    const { now } = await currentPlant(env);
    const record = { ...b, uploaded_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"), sim_time: isoNaive(now) };
    const text = JSON.stringify(record);
    const task = b.task ?? ((b.meta as { task?: string } | undefined)?.task ?? null);
    await env.DB.prepare("INSERT OR REPLACE INTO model_artifacts(run_id, task, seed, uploaded_at, sim_time, body) VALUES (?, ?, ?, ?, ?, ?)")
      .bind(b.run_id, task, b.seed, record.uploaded_at, record.sim_time, text).run();
    return json({ run_id: b.run_id, stored: "d1", bytes: text.length }, 201);
  }

  const art = /^\/admin\/model_artifacts\/([^/]+)$/.exec(path);
  if (art && method === "GET") {
    requireAdmin(req, env);
    const row = await env.DB.prepare("SELECT body FROM model_artifacts WHERE run_id=?").bind(decodeURIComponent(art[1])).first<{ body: string }>();
    if (!row) throw new HttpError(404, "no such artefact");
    return new Response(row.body, { headers: { "content-type": "application/json" } });
  }

  throw new HttpError(404, "Not Found");
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    try {
      return await handle(req, env);
    } catch (e) {
      if (e instanceof HttpError) return json({ detail: e.message }, e.status);
      console.error(e);
      return json({ detail: "internal error", error: String((e as Error).message ?? e) }, 500);
    }
  },
} satisfies ExportedHandler<Env>;
