import { SELF, env } from "cloudflare:test";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import fixture from "./fixtures/scoring.json";
import worker, { scoreOnce } from "../src/index";
import type { Env, Fetcher } from "../src/index";
import { HOUR_MS, ms } from "../src/score";

const testEnv = (over: Partial<Env> = {}): Env => ({ ...(env as unknown as Env), ...over });
const readings = fixture.readings as Record<string, (number | null)[]>;
const [before, after] = fixture.cases; // 2024-09-20 (BFP2 degrading), 2024-10-03 (after its repair)

/** Fake plant API at the fixture's sim time. */
function fakeApi(simTime = before.as_of): Fetcher {
  const assets: Record<string, string[]> = {};
  for (const tag of Object.keys(readings)) (assets[tag.split(".")[0]] ??= []).push(tag);
  return async (url, init) => {
    if ((init?.headers as Record<string, string>)?.authorization !== "Bearer r") return new Response("no", { status: 401 });
    const p = new URL(url).pathname;
    if (p === "/clock") return Response.json({ sim_time: simTime, speed: 60 });
    if (p === "/assets") return Response.json(Object.entries(assets).map(([asset_id, tags]) => ({ asset_id, tags })));
    if (p === "/maintenance/log") return Response.json(fixture.repairs.filter((r) => ms(r.timestamp) <= ms(simTime))); // past only, like the API
    return new Response("nope", { status: 404 });
  };
}

async function count(sql: string): Promise<number> {
  return (await env.DB.prepare(sql).first<{ n: number }>())?.n ?? 0;
}

beforeAll(async () => {
  // readings: 24 tags x 1440 hours from the fixture; D1 allows 100 bound parameters per statement
  const first = ms(fixture.first_hour);
  const rows = Object.entries(readings).flatMap(([tag, vals]) =>
    vals.map((v, i) => [tag, new Date(first + i * HOUR_MS).toISOString().slice(0, 19), v] as const),
  );
  const stmts = [];
  for (let i = 0; i < rows.length; i += 33) {
    const chunk = rows.slice(i, i + 33);
    stmts.push(env.DB.prepare(`INSERT INTO readings(tag, ts, value) VALUES ${chunk.map(() => "(?, ?, ?)").join(", ")}`).bind(...chunk.flat()));
  }
  for (let i = 0; i < stmts.length; i += 200) await env.DB.batch(stmts.slice(i, i + 200));
  // a stray row far past the horizon must not be read (index-bounded window)
  await env.DB.prepare("INSERT INTO readings(tag, ts, value) VALUES ('BFP2.VIB_DE', '2204-07-27T16:00:00', 99)").run();
});

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM runs"),
    env.DB.prepare("DELETE FROM predictions"),
    env.DB.prepare("DELETE FROM alerts"),
    env.DB.prepare("DELETE FROM maintenance_log"),
    env.DB.prepare("DELETE FROM model_artifacts"),
  ]);
  const a = fixture.artifact;
  const predict = { run_id: a.run_id, seed: 42, model: a, meta: { task: "predict", horizon_days: a.horizon_days } };
  const anomaly = { run_id: "anomaly_fleet_2024-09-20_r1", seed: 42, model: { task: "anomaly", config: fixture.anomaly_config }, meta: { task: "anomaly" } };
  const ins = env.DB.prepare("INSERT INTO model_artifacts(run_id, task, seed, uploaded_at, sim_time, body) VALUES (?, ?, 42, '2026-09-08T00:00:00Z', NULL, ?)");
  await env.DB.batch([ins.bind(predict.run_id, "predict", JSON.stringify(predict)), ins.bind(anomaly.run_id, "anomaly", JSON.stringify(anomaly))]);
});

describe("scoreOnce", () => {
  it("scores one sim day: runs, predictions, alerts as episodes; reads only the lookback window", async () => {
    const r = await scoreOnce(testEnv(), { fetcher: fakeApi() });
    expect(r.as_of).toBe(before.as_of);
    expect(r.anomaly).toMatchObject({ artifact: "anomaly_fleet_2024-09-20_r1", flags: before.expected.anomaly.length });
    const pred = r.predict as { p_fail: Record<string, number>; features_at: string; alerts: number; workorders: number };
    expect(pred.features_at).toBe("2024-09-20T00:00:00");
    for (const p of before.expected.predict) expect(pred.p_fail[p.asset]).toBeCloseTo(p.p_fail, 9);
    expect(pred.alerts).toBe(0); // PREDICT_ACTIONS=off
    expect(pred.workorders).toBe(0);

    expect(await count("SELECT COUNT(*) AS n FROM runs")).toBe(2);
    expect(await count("SELECT COUNT(*) AS n FROM predictions")).toBe(4);
    const alerts = await env.DB.prepare("SELECT tag FROM alerts ORDER BY tag, first_flag_ts").all<{ tag: string }>();
    expect(alerts.results.length).toBe((r.anomaly as { episodes: number }).episodes);
    expect(alerts.results.length).toBeLessThan(before.expected.anomaly.length); // 9 runs roll up into fewer episodes
    expect(new Set(alerts.results.map((a) => a.tag))).toEqual(new Set(["BFP2.VIB_DE", "BFP2.BRG_TEMP_DE"]));
    expect(await count("SELECT COUNT(*) AS n FROM maintenance_log")).toBe(0);

    const tags = Object.keys(readings).length;
    expect(r.rows_read).toBeGreaterThanOrEqual(tags * 1440);
    expect(r.rows_read).toBeLessThan(tags * 1440 + 200); // nothing outside the window, e.g. not the 2204 row
  });

  it("is a no-op for a sim day already scored; force re-runs extend alerts instead of duplicating them", async () => {
    await scoreOnce(testEnv(), { fetcher: fakeApi() });
    const before = await count("SELECT COUNT(*) AS n FROM alerts");
    const again = await scoreOnce(testEnv(), { fetcher: fakeApi("2024-09-20T18:30:00") });
    expect(again.skipped).toMatch(/already scored/);
    expect(again.rows_read).toBeLessThanOrEqual(1);
    await scoreOnce(testEnv(), { fetcher: fakeApi(), force: true });
    expect(await count("SELECT COUNT(*) AS n FROM alerts")).toBe(before);
    expect(await count("SELECT COUNT(*) AS n FROM runs")).toBe(2);
  });

  it("raises one predict alert and one work order per asset when PREDICT_ACTIONS=on", async () => {
    const on = testEnv({ PREDICT_ACTIONS: "on" });
    const r = await scoreOnce(on, { fetcher: fakeApi() });
    const hot = before.expected.predict.filter((p) => p.alert).map((p) => p.asset); // 2 consecutive days above threshold
    expect(hot).toContain("BFP2");
    expect((r.predict as { alerts: number }).alerts).toBe(hot.length);
    const wos = await env.DB.prepare("SELECT wo_id, asset_id, source, description FROM maintenance_log WHERE kind='workorder' ORDER BY wo_id")
      .all<{ wo_id: string; asset_id: string; source: string; description: string }>();
    expect(wos.results.map((w) => w.asset_id)).toEqual(hot);
    expect(wos.results.every((w) => w.source === "scoring")).toBe(true);
    expect(wos.results[0].wo_id).toBe("WO-00001");
    expect(wos.results.find((w) => w.asset_id === "BFP2")!.description).toMatch(/bearing_wear/);
    await scoreOnce(on, { fetcher: fakeApi(), force: true });
    expect(await count("SELECT COUNT(*) AS n FROM maintenance_log WHERE kind='workorder'")).toBe(hot.length);
    expect(await count("SELECT COUNT(*) AS n FROM alerts WHERE kind='predict'")).toBe(hot.length);
  });

  it("a repair resolves the asset's open alerts and scoring work orders; the ended episode stays resolved", async () => {
    const on = testEnv({ PREDICT_ACTIONS: "on" });
    await scoreOnce(on, { fetcher: fakeApi(before.as_of) });
    const openBefore = await count("SELECT COUNT(*) AS n FROM alerts WHERE asset_id = 'BFP2' AND status = 'open'");
    expect(openBefore).toBeGreaterThan(0);
    expect(await count("SELECT COUNT(*) AS n FROM maintenance_log WHERE asset_id = 'BFP2' AND status = 'open'")).toBe(1);

    const r = await scoreOnce(on, { fetcher: fakeApi(after.as_of) });
    expect(r.anomaly).toMatchObject({ closed_by_repair: openBefore });
    expect(await count("SELECT COUNT(*) AS n FROM alerts WHERE asset_id = 'BFP2' AND status = 'open'")).toBe(0);
    expect(await count("SELECT COUNT(*) AS n FROM maintenance_log WHERE asset_id = 'BFP2' AND status = 'done' AND source = 'scoring'")).toBe(1);
    expect((r.predict as { p_fail: Record<string, number> }).p_fail.BFP2).toBeCloseTo(after.expected.predict.find((p) => p.asset === "BFP2")!.p_fail, 9);
    expect((r.predict as { alerts: number }).alerts).toBe(0);

    // scored fresh after the repair (no earlier run): the pre-repair episode is recorded, already resolved
    await env.DB.batch([env.DB.prepare("DELETE FROM alerts"), env.DB.prepare("DELETE FROM runs")]);
    const fresh = await scoreOnce(on, { fetcher: fakeApi(after.as_of) });
    expect((fresh.anomaly as { recorded_resolved: number }).recorded_resolved).toBeGreaterThan(0);
    expect(await count("SELECT COUNT(*) AS n FROM alerts WHERE status = 'open'")).toBe(0);
  });

  it("records anomaly only when no predict artefact is stored", async () => {
    await env.DB.prepare("DELETE FROM model_artifacts WHERE task='predict'").run();
    const r = await scoreOnce(testEnv(), { fetcher: fakeApi() });
    expect(r.predict).toEqual({ skipped: "no predict artefact in D1" });
    expect(await count("SELECT COUNT(*) AS n FROM runs")).toBe(1);
  });

  it("fails loudly when the plant API rejects the token", async () => {
    await expect(scoreOnce(testEnv({ READ_TOKEN: "wrong" }), { fetcher: fakeApi() })).rejects.toThrow(/HTTP 401/);
  });
});

describe("http surface", () => {
  it("health answers 503 with the reason when D1 fails (not error 1101)", async () => {
    const broken = { ...env, DB: { prepare() { throw new Error("D1_ERROR: Your account has exceeded D1's free tier daily row read limit."); } } };
    const r = await worker.fetch(new Request("http://x/health"), broken as never, {} as never);
    expect(r.status).toBe(503);
    const b = (await r.json()) as { status: string; d1_limit_exceeded: boolean; service: string };
    expect(b).toMatchObject({ status: "degraded", d1_limit_exceeded: true, service: "plant-scoring" });
  });

  it("health is open; POST /score needs the admin token", async () => {
    const h = await SELF.fetch("http://scoring/health");
    expect(h.status).toBe(200);
    expect(((await h.json()) as { service: string }).service).toBe("plant-scoring");
    expect((await SELF.fetch("http://scoring/score", { method: "POST" })).status).toBe(403);
    expect((await SELF.fetch("http://scoring/score", { method: "POST", headers: { authorization: "Bearer r" } })).status).toBe(403);
  });
});
