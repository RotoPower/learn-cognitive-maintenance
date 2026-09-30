import { SELF, env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import worker, { ingestOnce } from "../src/index";
import * as mainModule from "../src/index";
import type { Env, Fetcher } from "../src/index";

// workerd refuses to start a Worker whose main module exports anything but handlers or classes
// (vitest and `wrangler deploy --dry-run` do not check this; `export const SENTINEL` slipped through).
it("main module exports only handlers and functions", () => {
  for (const [name, value] of Object.entries(mainModule)) {
    if (name === "default") continue;
    expect(typeof value, name).toBe("function");
  }
});

const TAGS = ["PLANT.LOAD", "GT1.EXH_TEMP", "BFP1.FLOW", "BFP2.VIB_DE", "TX1.MOISTURE_PPM"]; // last: the sentinel
const HOUR = 3_600_000;
const iso = (ms: number) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "");

/** Fake plant API: deterministic values, BFP1 in outage (null) at 2024-07-20. */
function fakeApi(simHour: string, tags: string[] = TAGS) {
  const calls: string[] = [];
  const value = (tag: string, ts: string) => (tag === "BFP1.FLOW" && ts.startsWith("2024-07-20") ? null : tag.length + Date.parse(ts + "Z") / HOUR / 1e6);
  const fetcher: Fetcher = async (url, init) => {
    calls.push(url);
    const auth = (init?.headers as Record<string, string>)?.authorization;
    if (auth !== "Bearer r") return new Response(JSON.stringify({ detail: "READ or ADMIN token required" }), { status: 401 });
    const u = new URL(url);
    if (u.pathname === "/tags/latest") {
      const values: Record<string, number | null> = {};
      for (const t of tags) values[t] = value(t, simHour);
      return Response.json({ timestamp: simHour, values });
    }
    const m = /^\/tags\/([^/]+)\/history$/.exec(u.pathname);
    if (m) {
      const tag = decodeURIComponent(m[1]);
      const from = Date.parse(u.searchParams.get("from")! + "Z"), to = Date.parse(u.searchParams.get("to")! + "Z");
      const points = [];
      for (let t = from; t <= to; t += HOUR) points.push({ timestamp: iso(t), value: value(tag, iso(t)) });
      return Response.json({ tag, points });
    }
    return new Response("nope", { status: 404 });
  };
  return { fetcher, calls };
}

const testEnv = (): Env => env as unknown as Env;
const countRows = async () => (await env.DB.prepare("SELECT COUNT(*) AS n FROM readings").first<{ n: number }>())?.n ?? 0;

beforeEach(async () => {
  await env.DB.prepare("DELETE FROM readings").run();
});

describe("ingestOnce", () => {
  it("writes one row per tag for the current sim hour", async () => {
    const { fetcher, calls } = fakeApi("2024-09-01T05:00:00");
    const r = await ingestOnce(testEnv(), fetcher);
    expect(r.sim_hour).toBe("2024-09-01T05:00:00");
    expect(r.inserted_latest).toBe(TAGS.length);
    expect(r.backfilled_hours).toBe(0);
    expect(await countRows()).toBe(TAGS.length);
    expect(calls.length).toBe(1); // no history calls on the first run
    const rows = await env.DB.prepare("SELECT tag, ts, value FROM readings ORDER BY tag").all<{ tag: string; ts: string; value: number }>();
    expect(rows.results.map((x) => x.tag)).toEqual([...TAGS].sort());
    expect(rows.results.every((x) => x.ts === "2024-09-01T05:00:00")).toBe(true);
  });

  it("is idempotent on (tag, ts): repeated runs in the same sim hour do not add rows", async () => {
    const { fetcher } = fakeApi("2024-09-01T05:00:00");
    await ingestOnce(testEnv(), fetcher);
    await ingestOnce(testEnv(), fetcher);
    const r = await ingestOnce(testEnv(), fetcher);
    expect(r.inserted_latest).toBe(0); // hour already stored: no writes (paused or clamped clock)
    expect(await countRows()).toBe(TAGS.length);
    const n = await env.DB.prepare("SELECT COUNT(*) AS n FROM readings WHERE tag='GT1.EXH_TEMP'").first<{ n: number }>();
    expect(n?.n).toBe(1);
  });

  it("advances hour by hour without backfill calls", async () => {
    await ingestOnce(testEnv(), fakeApi("2024-09-01T05:00:00").fetcher);
    const { fetcher, calls } = fakeApi("2024-09-01T06:00:00");
    const r = await ingestOnce(testEnv(), fetcher);
    expect(r.backfilled_hours).toBe(0);
    expect(calls.length).toBe(1);
    expect(await countRows()).toBe(2 * TAGS.length);
  });

  it("backfills the gap after a clock jump, bounded by MAX_BACKFILL_HOURS", async () => {
    await ingestOnce(testEnv(), fakeApi("2024-09-01T00:00:00").fetcher);
    const { fetcher, calls } = fakeApi("2024-09-01T10:00:00"); // 9 missing hours: 01..09
    const r = await ingestOnce(testEnv(), fetcher);
    expect(r.backfilled_hours).toBe(9);
    expect(r.backfilled_rows).toBe(9 * TAGS.length);
    expect(r.skipped_backfill_hours).toBe(0);
    expect(calls.filter((c) => c.includes("/history")).length).toBe(TAGS.length);
    expect(await countRows()).toBe(11 * TAGS.length);
    const hours = await env.DB.prepare("SELECT DISTINCT ts FROM readings ORDER BY ts").all<{ ts: string }>();
    expect(hours.results.map((h) => h.ts.slice(11, 13))).toEqual(["00", "01", "02", "03", "04", "05", "06", "07", "08", "09", "10"]);

    // a huge jump is capped
    const big = { ...testEnv(), MAX_BACKFILL_HOURS: "3" } as Env;
    const r2 = await ingestOnce(big, fakeApi("2024-09-03T10:00:00").fetcher); // 47 missing hours
    expect(r2.backfilled_hours).toBe(3);
    expect(r2.skipped_backfill_hours).toBe(44);
  });

  it("seeds trailing history on the very first run when INITIAL_BACKFILL_HOURS is set", async () => {
    const seeded = { ...testEnv(), INITIAL_BACKFILL_HOURS: "5" } as Env;
    const { fetcher, calls } = fakeApi("2024-09-01T10:00:00");
    const r = await ingestOnce(seeded, fetcher);
    expect(r.backfilled_hours).toBe(5);
    expect(await countRows()).toBe(6 * TAGS.length); // 05..09 seeded + 10 current
    expect(calls.filter((c) => c.includes("/history")).length).toBe(TAGS.length);
    // second run: no gap, no more history calls
    const again = fakeApi("2024-09-01T11:00:00");
    const r2 = await ingestOnce(seeded, again.fetcher);
    expect(r2.backfilled_hours).toBe(0);
    expect(again.calls.length).toBe(1);
  });

  it("stores outage readings as NULL", async () => {
    await ingestOnce(testEnv(), fakeApi("2024-07-20T03:00:00").fetcher);
    const row = await env.DB.prepare("SELECT value FROM readings WHERE tag='BFP1.FLOW'").first<{ value: number | null }>();
    expect(row?.value).toBeNull();
    const other = await env.DB.prepare("SELECT value FROM readings WHERE tag='BFP2.VIB_DE'").first<{ value: number | null }>();
    expect(other?.value).not.toBeNull();
  });

  it("rewrites hours stored by the 4-asset plant (no sentinel) and fills the gap before them", async () => {
    // Staging after the full-plant merge: the demo reached 09-15 with all tags; old 4-asset rows
    // (PLANT.LOAD but no sentinel) start at 09-21 13:00. The jump to 09-22 must fill 09-15..09-21.
    const wide = { ...testEnv(), MAX_BACKFILL_HOURS: "200" } as Env;
    await ingestOnce(wide, fakeApi("2024-09-15T00:00:00").fetcher);
    const old = env.DB.prepare("INSERT INTO readings(tag, ts, value) VALUES (?, ?, 1)");
    const oldHours = Array.from({ length: 12 }, (_, i) => iso(Date.parse("2024-09-21T13:00:00Z") + i * HOUR));
    await env.DB.batch(oldHours.flatMap((ts) => ["PLANT.LOAD", "BFP2.VIB_DE"].map((tag) => old.bind(tag, ts))));

    const r = await ingestOnce(wide, fakeApi("2024-09-22T00:00:00").fetcher);
    expect(r.inserted_latest).toBe(TAGS.length); // 09-22 00:00 had old rows only
    expect(r.backfilled_hours).toBe(7 * 24 - 1); // 09-15 01:00 .. 09-21 23:00
    const n = await env.DB.prepare("SELECT COUNT(*) AS n FROM readings WHERE tag = ? AND ts >= '2024-09-15T00:00:00'").bind("GT1.EXH_TEMP").first<{ n: number }>();
    expect(n?.n).toBe(7 * 24 + 1); // every hour now has the tags the old plant lacked
    expect(await countRows()).toBe((7 * 24 + 1) * TAGS.length); // old rows replaced, not duplicated
  });

  it("refuses a plant API without the sentinel tag instead of rewriting a week every pass", async () => {
    const fourAsset = TAGS.filter((t) => t !== "TX1.MOISTURE_PPM");
    await expect(ingestOnce(testEnv(), fakeApi("2024-09-01T05:00:00", fourAsset).fetcher)).rejects.toThrow(/no TX1\.MOISTURE_PPM/);
    expect(await countRows()).toBe(0);
  });

  it("fails loudly when the API rejects the token", async () => {
    const bad = { ...testEnv(), READ_TOKEN: "wrong" } as Env;
    await expect(ingestOnce(bad, fakeApi("2024-09-01T05:00:00").fetcher)).rejects.toThrow(/HTTP 401/);
    const n = await env.DB.prepare("SELECT COUNT(*) AS n FROM readings").first<{ n: number }>();
    expect(n?.n).toBe(0);
  });
});

describe("http surface", () => {
  it("health answers 503 with the reason when D1 fails (not error 1101)", async () => {
    const broken = { ...env, DB: { prepare() { throw new Error("D1_ERROR: Your account has exceeded D1's free tier daily row read limit."); } } };
    const r = await worker.fetch(new Request("http://x/health"), broken as never, {} as never);
    expect(r.status).toBe(503);
    const b = (await r.json()) as { status: string; d1_limit_exceeded: boolean; service: string };
    expect(b).toMatchObject({ status: "degraded", d1_limit_exceeded: true, service: "plant-ingest" });
  });

  it("health is open and reports the last ingested hour", async () => {
    await ingestOnce(testEnv(), fakeApi("2024-09-01T05:00:00").fetcher);
    const r = await SELF.fetch("http://ingest/health");
    expect(r.status).toBe(200);
    const b = (await r.json()) as { last_ingested_hour: string; sentinel_tag: string };
    expect(b.last_ingested_hour).toBe("2024-09-01T05:00:00");
    expect(b.sentinel_tag).toBe("TX1.MOISTURE_PPM");
  });

  it("manual trigger needs the admin token", async () => {
    expect((await SELF.fetch("http://ingest/ingest", { method: "POST" })).status).toBe(403);
    expect((await SELF.fetch("http://ingest/ingest", { method: "POST", headers: { authorization: "Bearer r" } })).status).toBe(403);
    // the admin path itself is exercised by the ingestOnce tests above with an injected fetcher
  });
});
