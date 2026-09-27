import path from "node:path";
import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

// Distinctive tokens so the tests can assert none of them ever reaches a response.
const READ = "read-SECRET-token";
const ADMIN = "admin-SECRET-token";

/** Stateful fake of the plant API (clock, assets, log, history, admin) behind the PLANT binding. */
function fakePlant() {
  const start = Date.parse("2024-09-20T13:00:00Z");
  let now = start;
  const injected: { asset: string; mode: string }[] = [];
  const iso = (ms: number) => new Date(ms).toISOString().slice(0, 19);
  const tags: Record<string, string[]> = {
    GT1: ["GT1.EXH_TEMP", "GT1.BRG_TEMP_2"], BFP1: ["BFP1.VIB_DE"], BFP2: ["BFP2.VIB_DE", "BFP2.BRG_TEMP_DE"], CTF1: ["CTF1.VIB"], PLANT: ["PLANT.LOAD"],
  };
  return async (req: Request): Promise<Response> => {
    const url = new URL(req.url);
    const auth = req.headers.get("authorization");
    const admin = auth === `Bearer ${ADMIN}`;
    if (auth !== `Bearer ${READ}` && !admin) return Response.json({ detail: "READ or ADMIN token required" }, { status: 401 });
    const p = url.pathname;
    if (p === "/clock") return Response.json({ sim_time: iso(now), speed: 60, horizon_end: "2024-12-31T00:00:00" });
    if (p === "/assets") return Response.json(Object.entries(tags).map(([asset_id, t]) => ({ asset_id, description: `${asset_id} asset`, tags: t })));
    if (p === "/maintenance/log") {
      const a = url.searchParams.get("asset_id");
      const log = [{ kind: "corrective_repair", asset_id: "BFP1", timestamp: "2024-06-01T00:00:00" }];
      return Response.json(log.filter((e) => !a || e.asset_id === a));
    }
    const h = /^\/tags\/([^/]+)\/history$/.exec(p);
    if (h) {
      const tag = decodeURIComponent(h[1]);
      const from = Date.parse(url.searchParams.get("from") + "Z"), to = Date.parse(url.searchParams.get("to") + "Z");
      const points = [];
      for (let t = from; t <= to; t += 3 * 3_600_000) points.push({ timestamp: iso(t), value: tag.endsWith("BRG_TEMP_2") ? 81.4 : 1 + ((t / 3_600_000) % 24) / 10 });
      return Response.json({ tag, interval: "3h", points });
    }
    if (!admin) return Response.json({ detail: "ADMIN token required" }, { status: 403 });
    const body = req.method === "POST" ? ((await req.json().catch(() => ({}))) as Record<string, unknown>) : {};
    if (p === "/clock/jump") { now = Date.parse(String(body.to) + "Z"); return Response.json({ sim_time: iso(now), speed: 60 }); }
    if (p === "/admin/reset") { now = start; injected.length = 0; return Response.json({ seed: 42, sim_time: iso(now), speed: 60 }); }
    if (p === "/admin/inject_fault") {
      if (injected.some((i) => i.asset === body.asset)) return Response.json({ detail: "overlaps an existing scenario" }, { status: 409 });
      injected.push({ asset: String(body.asset), mode: String(body.mode) });
      return Response.json({ injected: body }, { status: 201 });
    }
    if (p === "/admin/ground_truth") return Response.json({ failures: ["must never be proxied"] });
    return Response.json({ detail: "Not Found" }, { status: 404 });
  };
}

export default defineConfig(async () => {
  // The schema is owned by apps/plant-api; apply its migrations to the test database.
  const migrations = await readD1Migrations(path.join(import.meta.dirname, "..", "plant-api", "migrations"));
  return {
    plugins: [
      cloudflareTest({
        wrangler: { configPath: "./wrangler.toml" },
        miniflare: {
          bindings: { TEST_MIGRATIONS: migrations, READ_TOKEN: READ, ADMIN_TOKEN: ADMIN, CACHE_SECONDS: "0" },
          serviceBindings: {
            PLANT: fakePlant(),
            SCORING: async (req: Request) =>
              req.headers.get("authorization") === `Bearer ${ADMIN}`
                ? Response.json({ as_of: "2024-09-20T13:00:00", anomaly: { flags: 0 } })
                : Response.json({ detail: "ADMIN token required" }, { status: 403 }),
          },
        },
      }),
    ],
    test: { setupFiles: ["./test/apply-migrations.ts"] },
  };
});
