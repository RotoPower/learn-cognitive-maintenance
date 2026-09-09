/**
 * Ingest Worker (module D2.2).
 *
 * Cron `* * * * *`: read `/tags/latest` (all assets, top of the current sim hour)
 * from the plant API and upsert one row per tag into D1 `readings`. The primary
 * key (tag, ts) makes every run idempotent: at speed 60 the same sim hour is
 * seen many times and written once; at speed 3600 each minute is a new hour.
 *
 * Gap handling: if the last ingested hour is more than one hour behind the sim
 * clock (a clock jump, or missed crons), the missing hours are backfilled from
 * `/tags/{tag}/history`, bounded by MAX_BACKFILL_HOURS. Outage readings are
 * stored as NULL, exactly as the API reports them.
 *
 * HTTP: GET /health (status, last ingested hour, row count) is open;
 * POST /ingest (manual trigger) needs the ADMIN token.
 */

export interface Env {
  DB: D1Database;
  PLANT_API_URL: string;
  /** Service Binding to the plant API Worker (staging/production). Worker-to-Worker
   *  calls over a public workers.dev URL fail with Cloudflare error 1042, so when the
   *  binding is present it is used instead of the network. Absent in local dev/tests. */
  PLANT?: { fetch(input: string | Request, init?: RequestInit): Promise<Response> };
  READ_TOKEN?: string;
  ADMIN_TOKEN?: string;
  MAX_BACKFILL_HOURS?: string;
  /** Hours of history to seed when the readings table is empty (default 168). */
  INITIAL_BACKFILL_HOURS?: string;
}

export type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

/** Pick the transport: injected fetcher (tests) > service binding > global fetch. */
function transport(env: Env, injected?: Fetcher): Fetcher {
  if (injected) return injected;
  if (env.PLANT) return (url, init) => env.PLANT!.fetch(url, init);
  return (url, init) => fetch(url, init);
}

const UA = "plant-ingest/0.1 (+https://github.com/RotoPower/learn-cognitive-maintenance)";
const HOUR_MS = 3_600_000;

interface Latest {
  timestamp: string;
  values: Record<string, number | null>;
}

interface History {
  tag: string;
  points: { timestamp: string; value: number | null }[];
}

export interface IngestResult {
  sim_hour: string;
  inserted_latest: number;
  backfilled_hours: number;
  backfilled_rows: number;
  skipped_backfill_hours: number;
  total_rows: number;
}

const iso = (ms: number): string => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "");
const ms = (isoNaive: string): number => Date.parse(isoNaive.endsWith("Z") ? isoNaive : `${isoNaive}Z`);

async function api<T>(env: Env, fetcher: Fetcher, path: string): Promise<T> {
  const r = await fetcher(`${env.PLANT_API_URL.replace(/\/+$/, "")}${path}`, {
    headers: { authorization: `Bearer ${env.READ_TOKEN ?? "read-token"}`, "user-agent": UA, accept: "application/json" },
  });
  if (!r.ok) throw new Error(`plant API ${path} -> HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return (await r.json()) as T;
}

async function upsert(env: Env, rows: { tag: string; ts: string; value: number | null }[]): Promise<number> {
  if (rows.length === 0) return 0;
  const stmt = env.DB.prepare("INSERT OR REPLACE INTO readings(tag, ts, value) VALUES (?, ?, ?)");
  // D1 batches are transactional and accept many statements; chunk to stay well under limits.
  for (let i = 0; i < rows.length; i += 100) {
    await env.DB.batch(rows.slice(i, i + 100).map((r) => stmt.bind(r.tag, r.ts, r.value)));
  }
  return rows.length;
}

/** One ingest pass. `fetcher` is injectable for tests. */
export async function ingestOnce(env: Env, injected?: Fetcher): Promise<IngestResult> {
  const fetcher = transport(env, injected);
  const latest = await api<Latest>(env, fetcher, "/tags/latest");
  const simHour = latest.timestamp;
  const tags = Object.keys(latest.values);

  const inserted = await upsert(
    env,
    tags.map((tag) => ({ tag, ts: simHour, value: latest.values[tag] })),
  );

  // Backfill: hours strictly between the previously newest row and this hour. On the
  // very first run (empty table) seed the trailing INITIAL_BACKFILL_HOURS so dashboards
  // have a trend from day one.
  let backfilledHours = 0, backfilledRows = 0, skipped = 0;
  const prev = await env.DB.prepare("SELECT MAX(ts) AS ts FROM readings WHERE ts < ?").bind(simHour).first<{ ts: string | null }>();
  const maxHours = Number(env.MAX_BACKFILL_HOURS ?? 168);
  const gapHours = prev?.ts ? Math.round((ms(simHour) - ms(prev.ts)) / HOUR_MS) - 1 : Number(env.INITIAL_BACKFILL_HOURS ?? 168);
  {
    if (gapHours > 0) {
      const hours = Math.min(gapHours, maxHours);
      skipped = gapHours - hours;
      const from = iso(ms(simHour) - hours * HOUR_MS);
      const to = iso(ms(simHour) - HOUR_MS);
      const rows: { tag: string; ts: string; value: number | null }[] = [];
      for (const tag of tags) {
        const h = await api<History>(env, fetcher, `/tags/${encodeURIComponent(tag)}/history?from=${from}&to=${to}&interval=1h`);
        for (const p of h.points) rows.push({ tag, ts: p.timestamp, value: p.value });
      }
      backfilledRows = await upsert(env, rows);
      backfilledHours = hours;
    }
  }

  const total = await env.DB.prepare("SELECT COUNT(*) AS n FROM readings").first<{ n: number }>();
  return {
    sim_hour: simHour,
    inserted_latest: inserted,
    backfilled_hours: backfilledHours,
    backfilled_rows: backfilledRows,
    skipped_backfill_hours: skipped,
    total_rows: total?.n ?? 0,
  };
}

async function health(env: Env): Promise<Response> {
  const last = await env.DB.prepare("SELECT MAX(ts) AS ts, COUNT(*) AS n, COUNT(DISTINCT tag) AS tags FROM readings").first<{ ts: string | null; n: number; tags: number }>();
  return Response.json({ service: "plant-ingest", last_ingested_hour: last?.ts ?? null, rows: last?.n ?? 0, tags: last?.tags ?? 0, plant_api: env.PLANT_API_URL });
}

export default {
  async scheduled(_controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(
      ingestOnce(env).then(
        (r) => console.log(JSON.stringify({ ingest: r })),
        (e) => console.error("ingest failed:", String(e)),
      ),
    );
  },

  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) return health(env);
    if (req.method === "POST" && url.pathname === "/ingest") {
      const auth = req.headers.get("authorization") ?? "";
      const tok = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : req.headers.get("x-api-key");
      if (tok !== (env.ADMIN_TOKEN ?? "admin-token")) return Response.json({ detail: "ADMIN token required" }, { status: 403 });
      try {
        return Response.json(await ingestOnce(env));
      } catch (e) {
        return Response.json({ detail: String((e as Error).message ?? e) }, { status: 502 });
      }
    }
    return Response.json({ detail: "Not Found" }, { status: 404 });
  },
} satisfies ExportedHandler<Env>;
