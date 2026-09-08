import { SELF, env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import { DEFAULT_FAULTS } from "../src/faults";
import { Plant } from "../src/sim";

const READ = { authorization: "Bearer r" };
const ADMIN = { authorization: "Bearer a" };
const JSONH = { "content-type": "application/json" };

const get = (path: string, headers: Record<string, string> = {}) => SELF.fetch(`http://plant${path}`, { headers });
const post = (path: string, body: unknown, headers: Record<string, string> = {}) =>
  SELF.fetch(`http://plant${path}`, { method: "POST", body: JSON.stringify(body), headers: { ...JSONH, ...headers } });

beforeAll(async () => {
  // deterministic start: seed 42, clock paused (PLANT_CLOCK_SPEED=0 from vitest.config) at 2024-09-01
  await post("/admin/reset", { seed: 42 }, ADMIN);
  await post("/clock/jump", { to: "2024-09-01T00:00:00" }, ADMIN);
});

describe("auth", () => {
  it("read routes need a token; admin routes need the admin token", async () => {
    expect((await get("/clock")).status).toBe(401);
    expect((await get("/clock", READ)).status).toBe(200);
    expect((await get("/clock", { "x-api-key": "r" })).status).toBe(200);
    expect((await get("/admin/ground_truth", READ)).status).toBe(403);
    expect((await get("/admin/ground_truth", ADMIN)).status).toBe(200);
    expect((await post("/clock/speed", { speed: 60 }, READ)).status).toBe(403);
  });
  it("index is open and lists routes", async () => {
    const r = await get("/");
    expect(r.status).toBe(200);
    expect(((await r.json()) as { routes: string[] }).routes).toContain("GET /tags/latest");
  });
});

describe("clock", () => {
  it("reports sim time and speed, jumps, and refuses out-of-horizon jumps", async () => {
    const c = (await (await get("/clock", READ)).json()) as { sim_time: string; speed: number };
    expect(c.sim_time).toBe("2024-09-01T00:00:00");
    expect(c.speed).toBe(0);
    const j = await post("/clock/jump", { to: "2024-10-01T00:00" }, ADMIN);
    expect(((await j.json()) as { sim_time: string }).sim_time).toBe("2024-10-01T00:00:00");
    expect((await post("/clock/jump", { to: "2030-01-01T00:00" }, ADMIN)).status).toBe(422);
    const s = await post("/clock/speed", { speed: 3600 }, ADMIN);
    expect(((await s.json()) as { speed: number }).speed).toBe(3600);
    await post("/clock/speed", { speed: 0 }, ADMIN);
    await post("/clock/jump", { to: "2024-09-01T00:00:00" }, ADMIN);
  });
});

describe("plant routes", () => {
  const plant = Plant.fromConfig(DEFAULT_FAULTS, 42);

  it("assets", async () => {
    const a = (await (await get("/assets", READ)).json()) as { asset_id: string; tags: string[] }[];
    expect(a.map((x) => x.asset_id)).toEqual(["GT1", "BFP1", "BFP2", "CTF1"]);
    expect(a[0].tags).toContain("GT1.EXH_TEMP");
  });

  it("history matches the simulator, clamps to now, handles intervals and errors", async () => {
    const r = await get("/tags/BFP2.VIB_DE/history?from=2024-08-30T00:00&to=2024-08-30T05:00&interval=1h", READ);
    expect(r.status).toBe(200);
    const pts = ((await r.json()) as { points: { timestamp: string; value: number }[] }).points;
    expect(pts.map((p) => p.timestamp)).toEqual([0, 1, 2, 3, 4, 5].map((h) => `2024-08-30T0${h}:00:00`));
    for (const p of pts) expect(p.value).toBeCloseTo(plant.value("BFP2", "VIB_DE", p.timestamp), 9);

    const fut = (await (await get("/tags/GT1.CDP/history?from=2024-08-31T22:00&to=2024-09-02T00:00", READ)).json()) as { points: { timestamp: string }[] };
    expect(fut.points.at(-1)?.timestamp).toBe("2024-09-01T00:00:00");
    expect(fut.points.length).toBe(3);

    const q = (await (await get("/tags/GT1.CDP/history?from=2024-08-01T00:00&to=2024-08-01T01:00&interval=15m", READ)).json()) as { points: unknown[] };
    expect(q.points.length).toBe(5);
    expect((await get("/tags/GT1.CDP/history?from=2024-08-01&to=2024-08-02&interval=bogus", READ)).status).toBe(422);
    expect((await get("/tags/GT1.NOPE/history?from=2024-08-01&to=2024-08-02", READ)).status).toBe(404);
    expect((await get("/tags/ZZ9.X/history?from=2024-08-01&to=2024-08-02", READ)).status).toBe(404);
    // hyphenated asset id in the tag is accepted
    expect((await get("/tags/BFP-2.VIB_DE/history?from=2024-08-01&to=2024-08-01T01:00", READ)).status).toBe(200);
  });

  it("outage reads null", async () => {
    const r = (await (await get("/tags/BFP1.FLOW/history?from=2024-07-20T00:00&to=2024-07-20T03:00", READ)).json()) as { points: { value: number | null }[] };
    expect(r.points.every((p) => p.value === null)).toBe(true);
  });

  it("latest is the top-of-hour scan", async () => {
    const r = (await (await get("/tags/latest?asset_id=BFP-2", READ)).json()) as { timestamp: string; values: Record<string, number> };
    expect(r.timestamp).toBe("2024-09-01T00:00:00");
    expect(Object.keys(r.values).sort()).toEqual(["BFP2.BRG_TEMP_DE", "BFP2.DISCH_PRESS", "BFP2.FLOW", "BFP2.MOTOR_CURR", "BFP2.VIB_DE", "BFP2.VIB_NDE", "PLANT.LOAD"]);
    expect(r.values["BFP2.FLOW"]).toBeCloseTo(plant.value("BFP2", "FLOW", "2024-09-01T00:00:00"), 9);
    const all = (await (await get("/tags/latest", READ)).json()) as { values: Record<string, number> };
    expect(Object.keys(all.values).length).toBe(1 + 7 + 6 + 6 + 4);
    expect((await get("/tags/latest?asset_id=NOPE", READ)).status).toBe(404);
  });

  it("work orders persist in D1 and the log shows past repairs only", async () => {
    expect(await (await get("/maintenance/log", READ)).json()).toEqual([]);
    const wo = await post("/maintenance/workorder", { asset_id: "bfp-2", type: "inspection", description: "vib check" }, READ);
    expect(wo.status).toBe(201);
    const w = (await wo.json()) as { id: string; asset_id: string; timestamp: string };
    expect(w.id).toBe("WO-00001");
    expect(w.asset_id).toBe("BFP2");
    expect(w.timestamp).toBe("2024-09-01T00:00:00");
    const log = (await (await get("/maintenance/log?asset_id=BFP2", READ)).json()) as { kind: string; id?: string }[];
    expect(log.length).toBe(1);
    expect(log[0].id).toBe("WO-00001");

    await post("/clock/jump", { to: "2024-10-01T00:00" }, ADMIN);
    const later = (await (await get("/maintenance/log", READ)).json()) as { kind: string; asset_id: string; timestamp: string }[];
    expect(later.map((e) => [e.kind, e.asset_id])).toEqual([["workorder", "BFP2"], ["corrective_repair", "BFP2"]]);
    expect(later[1].timestamp).toBe("2024-09-27T00:00:00");
    await post("/clock/jump", { to: "2024-09-01T00:00:00" }, ADMIN);
  });
});

describe("admin", () => {
  it("ground truth (API and D1 mirror)", async () => {
    const gt = (await (await get("/admin/ground_truth", ADMIN)).json()) as { seed: number; failures: { asset: string }[]; health_now: Record<string, number> };
    expect(gt.seed).toBe(42);
    expect(gt.failures.map((f) => f.asset)).toEqual(["BFP2", "GT1", "CTF1"]);
    expect(gt.health_now.BFP2).toBeLessThan(1); // 2024-09-01 is inside the BFP2 window
    expect(gt.health_now.GT1).toBe(1);
    const rows = await env.DB.prepare("SELECT kind, asset_id, mode FROM ground_truth ORDER BY id").all<{ kind: string; asset_id: string; mode: string }>();
    expect(rows.results.length).toBe(4);
    expect(rows.results.filter((r: { kind: string }) => r.kind === "event")[0].asset_id).toBe("BFP1");
  });

  it("inject fault: shows in ground truth and sensors; overlap refused", async () => {
    const body = { asset: "BFP-1", mode: "bearing_wear", onset: "2024-11-01T00:00", duration_days: 10 };
    const r = await post("/admin/inject_fault", body, ADMIN);
    expect(r.status).toBe(201);
    expect(((await r.json()) as { failure: string }).failure).toBe("2024-11-11T00:00:00");
    const gt = (await (await get("/admin/ground_truth", ADMIN)).json()) as { failures: { asset: string; mode: string }[] };
    expect(gt.failures.some((f) => f.asset === "BFP1" && f.mode === "bearing_wear")).toBe(true);

    await post("/clock/jump", { to: "2024-11-11T00:00" }, ADMIN);
    const mean = async (from: string, to: string) => {
      const p = ((await (await get(`/tags/BFP1.BRG_TEMP_DE/history?from=${from}&to=${to}`, READ)).json()) as { points: { value: number }[] }).points;
      return p.reduce((s, x) => s + x.value, 0) / p.length;
    };
    expect(await mean("2024-11-10T00:00", "2024-11-10T23:00")).toBeGreaterThan((await mean("2024-10-20T00:00", "2024-10-20T23:00")) + 10);

    expect((await post("/admin/inject_fault", { ...body, onset: "2024-11-05T00:00" }, ADMIN)).status).toBe(409);
    expect((await post("/admin/inject_fault", { ...body, mode: "nope" }, ADMIN)).status).toBe(422);
  });

  it("model artefacts round-trip through D1", async () => {
    const art = { run_id: "colab-a", seed: 42, model: { coefficients: [0.5] }, metrics: { auc: 0.9 }, meta: { task: "predict" } };
    expect((await post("/admin/model_artifacts", art, ADMIN)).status).toBe(201);
    expect(await (await get("/admin/model_artifacts", ADMIN)).json()).toEqual(["colab-a"]);
    const saved = (await (await get("/admin/model_artifacts/colab-a", ADMIN)).json()) as { model: { coefficients: number[] }; uploaded_at: string; task?: string };
    expect(saved.model.coefficients).toEqual([0.5]);
    expect(saved.uploaded_at).toMatch(/Z$/);
    expect((await post("/admin/model_artifacts", { run_id: "../evil", seed: 1 }, ADMIN)).status).toBe(422);
  });

  it("reset: new seed, injected faults and work orders gone, clock to start", async () => {
    const r = await post("/admin/reset", { seed: 7 }, ADMIN);
    const b = (await r.json()) as { seed: number; sim_time: string };
    expect(b.seed).toBe(7);
    expect(b.sim_time).toBe("2024-09-01T00:00:00"); // configured PLANT_CLOCK_START
    const gt = (await (await get("/admin/ground_truth", ADMIN)).json()) as { failures: unknown[] };
    expect(gt.failures.length).toBe(3);
    expect(await (await get("/maintenance/log", READ)).json()).toEqual([]);
    const p7 = Plant.fromConfig(DEFAULT_FAULTS, 7);
    const v = ((await (await get("/tags/GT1.EXH_TEMP/history?from=2024-03-01&to=2024-03-01", READ)).json()) as { points: { value: number }[] }).points[0].value;
    expect(v).toBeCloseTo(p7.value("GT1", "EXH_TEMP", "2024-03-01T00:00:00"), 9);
    await post("/admin/reset", { seed: 42 }, ADMIN);
  });
});
