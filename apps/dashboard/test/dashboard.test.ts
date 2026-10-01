import { SELF, env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

const api = (path: string, init?: RequestInit & { ip?: string }) =>
  SELF.fetch(`http://dash${path}`, { ...init, headers: { "content-type": "application/json", "cf-connecting-ip": init?.ip ?? "203.0.113.7", ...(init?.headers ?? {}) } });
const post = (path: string, body: unknown, ip?: string) => api(path, { method: "POST", body: JSON.stringify(body), ip });

async function text(r: Response): Promise<string> {
  const t = await r.text();
  expect(t).not.toMatch(/SECRET/); // no token ever reaches the browser
  return t;
}

beforeEach(async () => {
  await post("/api/demo/reset", {}, "10.0.0.250"); // fake plant back to 2024-09-20T13:00
  await env.DB.batch([
    env.DB.prepare("DELETE FROM demo_actions"), env.DB.prepare("DELETE FROM maintenance_log"),
    env.DB.prepare("DELETE FROM alerts"), env.DB.prepare("DELETE FROM predictions"), env.DB.prepare("DELETE FROM runs"),
  ]);
  const drivers = (d: [string, number][], interp: string, alert = false) => JSON.stringify({ drivers: d, interpretation: interp, alert, threshold: 0.256, artifact: "predict_fleet_h30_2024-10-15_s42" });
  await env.DB.batch([
    env.DB.prepare("INSERT INTO runs(run_id, task, as_of, artifact, started, finished) VALUES ('score_2024-09-20_predict', 'predict', '2024-09-20T13:00:00', 'p', 'x', 'y'), ('score_2024-09-20_anomaly', 'anomaly', '2024-09-20T13:00:00', 'a', 'x', 'y'), ('score_2024-10-30_predict', 'predict', '2024-10-30T00:00:00', 'p', 'x', 'y')"),
    env.DB.prepare("INSERT INTO predictions(run_id, asset_id, as_of, p_fail, horizon_days, drivers) VALUES (?, 'BFP2', '2024-09-20T00:00:00', 0.97, 30, ?), (?, 'GT1', '2024-09-20T00:00:00', 0.01, 30, ?), ('score_2024-10-30_predict', 'BFP2', '2024-10-30T00:00:00', 0.5, 30, '{}')")
      .bind("score_2024-09-20_predict", drivers([["VIB_DE__slope30d", 54]], "consistent with bearing_wear (VIB_DE)", true), "score_2024-09-20_predict", drivers([], "")),
    env.DB.prepare("INSERT INTO alerts(run_id, asset_id, tag, kind, first_flag_ts, last_flag_ts, severity, interpretation) VALUES ('r', 'BFP2', 'BFP2.VIB_DE', 'anomaly', '2024-09-15T00:00:00', '2024-09-19T12:00:00', 6.4, 'DE vibration rising'), ('r', 'BFP2', 'BFP2.BRG_TEMP_DE', 'anomaly', '2024-09-16T00:00:00', '2024-09-19T00:00:00', 4.1, 'DE bearing temperature rising'), ('r', 'CTF1', 'CTF1.VIB', 'anomaly', '2024-11-10T00:00:00', '2024-11-12T00:00:00', 5, 'future: must not show')"),
  ]);
});

describe("GET /api/overview", () => {
  it("builds KPIs and the fleet board from D1 and the plant API, as of the sim clock", async () => {
    const r = await api("/api/overview");
    expect(r.status).toBe(200);
    const o = JSON.parse(await text(r));
    expect(o.sim_time).toBe("2024-09-20T13:00:00");
    const byId = Object.fromEntries(o.assets.map((a: { asset_id: string }) => [a.asset_id, a]));
    // PLANT is not an asset card; cards come in process order, grouped by system
    expect(o.assets.map((a: { asset_id: string }) => a.asset_id)).toEqual(["GT1", "BFP1", "BFP2", "CWP1", "CTF1"]);
    expect(byId.CWP1).toMatchObject({ group: "Cooling water", modes: ["seal_leak", "bearing_wear"] });
    expect(byId.GT1).toMatchObject({ group: "Gas turbines", modes: ["compressor_fouling"] });
    expect(byId.BFP2).toMatchObject({ status: "critical", risk: 0.97, risk_alert: true, top_driver: "BFP2.VIB_DE", top_driver_source: "anomaly", open_alerts: 2 });
    expect(byId.CTF1.status).toBe("healthy"); // its alert lies in the sim future
    expect(byId.BFP1.days_since_maintenance).toBe(111);
    expect(byId.GT1.days_since_maintenance).toBeNull();
    expect(o.kpis).toMatchObject({ healthy: 4, warning: 0, critical: 1, predicted_failures_30d: 1, alerts_7d: 2, open_workorders: 0 });
    expect(o.predict).toMatchObject({ trusted: true, threshold: 0.256, run_id: "score_2024-09-20_predict" }); // not the future run
    expect(o.alerts.map((a: { tag: string }) => a.tag)).not.toContain("CTF1.VIB");
  });

  it("with a trusted model, risk alone sets status: above threshold is critical, above 0.75 of it a warning, below that healthy", async () => {
    await env.DB.prepare("INSERT OR REPLACE INTO predictions(run_id, asset_id, as_of, p_fail, horizon_days, drivers) VALUES ('score_2024-09-20_predict', 'BFP1', '2024-09-20T00:00:00', 0.3, 30, ?), ('score_2024-09-20_predict', 'CTF1', '2024-09-20T00:00:00', 0.2, 30, ?), ('score_2024-09-20_predict', 'GT1', '2024-09-20T00:00:00', 0.15, 30, ?)")
      .bind(JSON.stringify({ drivers: [["VIB_DE__slope7d", 1]], threshold: 0.256 }), JSON.stringify({ drivers: [], threshold: 0.256 }), JSON.stringify({ drivers: [], threshold: 0.256 })).run();
    const o = JSON.parse(await text(await api("/api/overview")));
    const byId = Object.fromEntries(o.assets.map((a: { asset_id: string }) => [a.asset_id, a]));
    expect(byId.BFP1).toMatchObject({ status: "critical", top_driver: "VIB_DE__slope7d", top_driver_source: "risk model" });
    expect(byId.CTF1.status).toBe("warning");
  });

  it("one day above the threshold without the persistence alert is yellow, not red", async () => {
    await env.DB.prepare("INSERT OR REPLACE INTO predictions(run_id, asset_id, as_of, p_fail, horizon_days, drivers) VALUES ('score_2024-09-20_predict', 'CTF1', '2024-09-20T00:00:00', 0.3, 30, ?)")
      .bind(JSON.stringify({ drivers: [["CTF1.X", 1]], threshold: 0.256, alert: false })).run();
    const o = JSON.parse(await text(await api("/api/overview")));
    const ctf = o.assets.find((x: { asset_id: string }) => x.asset_id === "CTF1");
    expect(ctf).toMatchObject({ status: "warning", risk_alert: false, top_driver: null });
  });
});

describe("GET /api/asset/:id", () => {
  it("returns 30-day trends from the plant API, alerts, prediction and interim actions", async () => {
    const r = await api("/api/asset/BFP2");
    expect(r.status).toBe(200);
    const a = JSON.parse(await text(r));
    expect(a.trends.map((t: { tag: string }) => t.tag)).toEqual(["BFP2.VIB_DE", "BFP2.BRG_TEMP_DE"]);
    expect(a.trends[0].points.length).toBe(241); // 30 days at 3 h, both ends
    expect(a.alerts.length).toBe(2);
    expect(a.prediction).toMatchObject({ p_fail: 0.97, trusted: true, interpretation: "consistent with bearing_wear (VIB_DE)" });
    expect(a.actions.source).toBe("interim");
    expect(a.mode).toBe("bearing_wear");
    const gt = JSON.parse(await text(await api("/api/asset/GT1")));
    expect(gt.trends.find((t: { tag: string }) => t.tag === "GT1.BRG_TEMP_2").dead).toBe(true);
  });

  it("uses the playbook from D1 when Part E has filled it", async () => {
    await env.DB.prepare("INSERT OR REPLACE INTO playbook(mode, section, body) VALUES ('bearing_wear', 'actions', '- Check it\n- Fix it')").run();
    const a = JSON.parse(await text(await api("/api/asset/BFP1")));
    expect(a.actions).toEqual({ source: "playbook", items: ["Check it", "Fix it"] });
    await env.DB.prepare("DELETE FROM playbook").run();
  });

  it("404 for unknown assets and never proxies ground truth", async () => {
    expect((await api("/api/asset/PLANT")).status).toBe(404);
    expect((await api("/api/admin/ground_truth")).status).toBe(404);
    expect((await api("/api/ground_truth")).status).toBe(404);
  });
});

describe("work orders", () => {
  it("draft first, then confirm; stored with source=demo", async () => {
    const draft = await post("/api/workorder", { asset_id: "BFP2", type: "inspection", description: "check DE bearing" });
    expect(await draft.json()).toMatchObject({ confirm_required: true, draft: { asset_id: "BFP2" } });
    expect((await env.DB.prepare("SELECT COUNT(*) AS n FROM maintenance_log").first<{ n: number }>())?.n).toBe(0);
    const ok = await post("/api/workorder", { asset_id: "BFP2", type: "inspection", description: "check DE bearing", confirm: true });
    expect(ok.status).toBe(200);
    expect(JSON.parse(await text(ok))).toMatchObject({ id: "WO-00001", source: "demo", timestamp: "2024-09-20T13:00:00" });
    const row = await env.DB.prepare("SELECT source, status FROM maintenance_log").first();
    expect(row).toEqual({ source: "demo", status: "open" });
    expect((await post("/api/workorder", { asset_id: "XX", confirm: true })).status).toBe(422);
  });
});

describe("demo controls", () => {
  it("jump +7 days moves the clock forward only, within the horizon", async () => {
    const r = await post("/api/demo/jump7", {});
    expect(r.status).toBe(200);
    expect(JSON.parse(await text(r)).result.sim_time).toBe("2024-09-27T13:00:00");
    expect((await post("/api/demo/jump", { day: 10 })).status).toBe(422); // backwards
    const end = await post("/api/demo/jump", { day: 366 });
    expect((await end.json() as { result: { sim_time: string } }).result.sim_time).toBe("2024-12-31T00:00:00"); // clamped
    const past = await post("/api/demo/jump", { day: 367 }); // the fake horizon ends on day 366
    expect(past.status).toBe(422);
    expect((await past.json() as { detail: string }).detail).toMatch(/1\.\.366/);
  });

  it("run scoring, inject fault (mode follows the asset), reset clears scoring outputs", async () => {
    const s = await post("/api/demo/score", {});
    expect(s.status).toBe(200);
    expect(JSON.parse(await text(s)).result.as_of).toBe("2024-09-20T13:00:00");
    const inj = await post("/api/demo/inject", { asset_id: "CTF1" });
    expect(inj.status).toBe(200);
    expect(JSON.parse(await text(inj)).result.injected).toMatchObject({ asset: "CTF1", mode: "gearbox_wear", onset: "2024-09-20T14:00:00" });
    const again = await post("/api/demo/inject", { asset_id: "CTF1" });
    expect(again.status).toBe(409);
    expect((await again.json() as { detail: string }).detail).toMatch(/overlaps/);
    const cwp = await post("/api/demo/inject", { asset_id: "CWP1", mode: "bearing_wear" });
    expect(JSON.parse(await text(cwp)).result.injected).toMatchObject({ asset: "CWP1", mode: "bearing_wear" });
    const wrong = await post("/api/demo/inject", { asset_id: "BFP1", mode: "seal_leak" });
    expect(wrong.status).toBe(422);
    expect((await wrong.json() as { detail: string }).detail).toMatch(/BFP1 can have bearing_wear/);
    const reset = await post("/api/demo/reset", {});
    expect(reset.status).toBe(200);
    expect((await env.DB.prepare("SELECT COUNT(*) AS n FROM alerts").first<{ n: number }>())?.n).toBe(0);
  });

  it("caps demo actions per viewer per hour, and scoring runs per day globally", async () => {
    for (let i = 0; i < 10; i++) expect((await post("/api/demo/score", {}, "198.51.100.1")).status).toBe(200); // the demo needs 7
    const eleventh = await post("/api/demo/score", {}, "198.51.100.1");
    expect(eleventh.status).toBe(429);
    expect((await eleventh.json() as { detail: string }).detail).toMatch(/per hour per viewer/);
    expect((await post("/api/demo/score", {}, "198.51.100.2")).status).toBe(200); // another viewer
    const stamp = new Date().toISOString();
    await env.DB.batch(Array.from({ length: 30 }, (_, i) => env.DB.prepare("INSERT INTO demo_actions(kind, viewer, ts) VALUES ('score', ?, ?)").bind(`v${i}`, stamp)));
    const capped = await post("/api/demo/score", {}, "198.51.100.3");
    expect(capped.status).toBe(429);
    expect((await capped.json() as { detail: string }).detail).toMatch(/per day/);
    const viewers = await env.DB.prepare("SELECT DISTINCT viewer FROM demo_actions WHERE viewer NOT LIKE 'v%'").all<{ viewer: string }>();
    expect(viewers.results.every((v) => /^[0-9a-f]{16}$/.test(v.viewer))).toBe(true); // hashed, never the IP
  });

  it("caps jumps into unstored weeks per day (D1 writes); jumps over stored weeks stay free", async () => {
    const stored = env.DB.prepare("INSERT OR REPLACE INTO readings(tag, ts, value) VALUES ('TX1.MOISTURE_PPM', ?, 1)");
    await env.DB.prepare("DELETE FROM readings").run();
    await stored.bind("2024-09-27T13:00:00").run(); // ingest already has the week after 09-20
    expect((await post("/api/demo/jump7", {}, "198.51.100.9")).status).toBe(200);
    const kinds = await env.DB.prepare("SELECT kind FROM demo_actions WHERE kind LIKE 'jump%'").all<{ kind: string }>();
    expect(kinds.results.map((k) => k.kind)).toEqual(["jump_stored"]);

    const stamp = new Date().toISOString();
    await env.DB.batch(Array.from({ length: 2 }, (_, i) => env.DB.prepare("INSERT INTO demo_actions(kind, viewer, ts) VALUES ('jump', ?, ?)").bind(`j${i}`, stamp)));
    const capped = await post("/api/demo/jump7", {}, "198.51.100.9"); // 10-04 is not stored
    expect(capped.status).toBe(429);
    expect((await capped.json() as { detail: string }).detail).toMatch(/new weeks are capped at 2 per day/);

    await stored.bind("2024-10-04T13:00:00").run();
    expect((await post("/api/demo/jump7", {}, "198.51.100.9")).status).toBe(200); // stored: not capped
    await env.DB.prepare("DELETE FROM readings").run();
  });

  it("unknown actions are 404", async () => {
    expect((await post("/api/demo/speed", { speed: 86400 })).status).toBe(404);
  });
});

describe("assistant proxy", () => {
  it("streams the assistant's NDJSON through, adding the secret and viewer IP server-side", async () => {
    const r = await post("/api/chat", { message: "What happened to BFP-2?", session_id: "sess-00000001" }, "203.0.113.50");
    expect(r.status).toBe(200);
    expect(r.headers.get("content-type")).toContain("ndjson");
    const events = (await text(r)).trim().split("\n").map((l) => JSON.parse(l));
    expect(events.map((e: { type: string }) => e.type)).toEqual(["tool", "text", "text", "done"]);
    expect(events[1].text).toBe("ip 203.0.113.50: ");
    const ok = await post("/api/chat/confirm", { session_id: "sess-00000001", draft_id: "d1" });
    expect(JSON.parse(await text(ok))).toEqual({ id: "WO-00009", status: "open" });
    expect((await post("/api/chat/confirm", { session_id: "sess-00000001", draft_id: "nope" })).status).toBe(404);
  });

  it("offline (503) when unset, unreachable or behind a stopped tunnel; a wrong secret is the operator's problem (502)", async () => {
    const { assistant } = await import("../src/index");
    const req = () => new Request("http://dash/api/chat", { method: "POST", body: JSON.stringify({ message: "hi", session_id: "sess-00000001" }) });
    const none = { ...env, ASSISTANT: undefined, ASSISTANT_URL: undefined } as never;
    await expect(assistant(none, req(), "/chat")).rejects.toMatchObject({ status: 503, message: expect.stringMatching(/offline/) });
    // a stopped Quick Tunnel: Cloudflare's 530 error page, or no connection at all
    const stale = { ...env, ASSISTANT: { fetch: async () => new Response("<html>1033</html>", { status: 530 }) } } as never;
    await expect(assistant(stale, req(), "/chat")).rejects.toMatchObject({ status: 503, message: expect.stringMatching(/offline/) });
    const down = { ...env, ASSISTANT: { fetch: async () => { throw new Error("connect refused"); } } } as never;
    await expect(assistant(down, req(), "/chat")).rejects.toMatchObject({ status: 503 });
    const wrong = { ...env, ASSISTANT_SECRET: "nope" } as never;
    await expect(assistant(wrong, req(), "/chat")).rejects.toMatchObject({ status: 502 });
  });
});
