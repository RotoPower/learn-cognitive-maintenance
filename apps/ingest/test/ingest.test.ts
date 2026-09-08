import { SELF, env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import { ingestOnce } from "../src/index";
import type { Env, Fetcher } from "../src/index";

const TAGS = ["PLANT.LOAD", "GT1.EXH_TEMP", "BFP1.FLOW", "BFP2.VIB_DE"];
const HOUR = 3_600_000;
const iso = (ms: number) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "");

/** Fake plant API: deterministic values, BFP1 in outage (null) at 2024-07-20. */
function fakeApi(simHour: string) {
  const calls: string[] = [];
  const value = (tag: string, ts: string) => (tag === "BFP1.FLOW" && ts.startsWith("2024-07-20") ? null : tag.length + Date.parse(ts + "Z") / HOUR / 1e6);
  const fetcher: Fetcher = async (url, init) => {
    calls.push(url);
    const auth = (init?.headers as Record<string, string>)?.authorization;
    if (auth !== "Bearer r") return new Response(JSON.stringify({ detail: "READ or ADMIN token required" }), { status: 401 });
    const u = new URL(url);
    if (u.pathname === "/tags/latest") {
      const values: Record<string, number | null> = {};
      for (const t of TAGS) values[t] = value(t, simHour);
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
    expect(r.total_rows).toBe(TAGS.length);
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
    expect(r.total_rows).toBe(TAGS.length);
    const n = await env.DB.prepare("SELECT COUNT(*) AS n FROM readings WHERE tag='GT1.EXH_TEMP'").first<{ n: number }>();
    expect(n?.n).toBe(1);
  });

  it("advances hour by hour without backfill calls", async () => {
    await ingestOnce(testEnv(), fakeApi("2024-09-01T05:00:00").fetcher);
    const { fetcher, calls } = fakeApi("2024-09-01T06:00:00");
    const r = await ingestOnce(testEnv(), fetcher);
    expect(r.backfilled_hours).toBe(0);
    expect(calls.length).toBe(1);
    expect(r.total_rows).toBe(2 * TAGS.length);
  });

  it("backfills the gap after a clock jump, bounded by MAX_BACKFILL_HOURS", async () => {
    await ingestOnce(testEnv(), fakeApi("2024-09-01T00:00:00").fetcher);
    const { fetcher, calls } = fakeApi("2024-09-01T10:00:00"); // 9 missing hours: 01..09
    const r = await ingestOnce(testEnv(), fetcher);
    expect(r.backfilled_hours).toBe(9);
    expect(r.backfilled_rows).toBe(9 * TAGS.length);
    expect(r.skipped_backfill_hours).toBe(0);
    expect(calls.filter((c) => c.includes("/history")).length).toBe(TAGS.length);
    expect(r.total_rows).toBe(11 * TAGS.length);
    const hours = await env.DB.prepare("SELECT DISTINCT ts FROM readings ORDER BY ts").all<{ ts: string }>();
    expect(hours.results.map((h) => h.ts.slice(11, 13))).toEqual(["00", "01", "02", "03", "04", "05", "06", "07", "08", "09", "10"]);

    // a huge jump is capped
    const big = { ...testEnv(), MAX_BACKFILL_HOURS: "3" } as Env;
    const r2 = await ingestOnce(big, fakeApi("2024-09-03T10:00:00").fetcher); // 47 missing hours
    expect(r2.backfilled_hours).toBe(3);
    expect(r2.skipped_backfill_hours).toBe(44);
  });

  it("stores outage readings as NULL", async () => {
    await ingestOnce(testEnv(), fakeApi("2024-07-20T03:00:00").fetcher);
    const row = await env.DB.prepare("SELECT value FROM readings WHERE tag='BFP1.FLOW'").first<{ value: number | null }>();
    expect(row?.value).toBeNull();
    const other = await env.DB.prepare("SELECT value FROM readings WHERE tag='BFP2.VIB_DE'").first<{ value: number | null }>();
    expect(other?.value).not.toBeNull();
  });

  it("fails loudly when the API rejects the token", async () => {
    const bad = { ...testEnv(), READ_TOKEN: "wrong" } as Env;
    await expect(ingestOnce(bad, fakeApi("2024-09-01T05:00:00").fetcher)).rejects.toThrow(/HTTP 401/);
    const n = await env.DB.prepare("SELECT COUNT(*) AS n FROM readings").first<{ n: number }>();
    expect(n?.n).toBe(0);
  });
});

describe("http surface", () => {
  it("health is open and reports the last ingested hour", async () => {
    await ingestOnce(testEnv(), fakeApi("2024-09-01T05:00:00").fetcher);
    const r = await SELF.fetch("http://ingest/health");
    expect(r.status).toBe(200);
    const b = (await r.json()) as { last_ingested_hour: string; rows: number; tags: number };
    expect(b.last_ingested_hour).toBe("2024-09-01T05:00:00");
    expect(b.rows).toBe(TAGS.length);
    expect(b.tags).toBe(TAGS.length);
  });

  it("manual trigger needs the admin token", async () => {
    expect((await SELF.fetch("http://ingest/ingest", { method: "POST" })).status).toBe(403);
    expect((await SELF.fetch("http://ingest/ingest", { method: "POST", headers: { authorization: "Bearer r" } })).status).toBe(403);
    // the admin path itself is exercised by the ingestOnce tests above with an injected fetcher
  });
});
