import path from "node:path";
import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

export default defineConfig(async () => {
  // The schema is owned by apps/plant-api; apply its migrations to the test database.
  const migrations = await readD1Migrations(path.join(import.meta.dirname, "..", "plant-api", "migrations"));
  return {
    plugins: [
      cloudflareTest({
        wrangler: { configPath: "./wrangler.toml" },
        miniflare: {
          bindings: { TEST_MIGRATIONS: migrations, READ_TOKEN: "r", ADMIN_TOKEN: "a", PLANT_API_URL: "http://plant", LOOKBACK_DAYS: "37", DATA_START: "2024-01-01T00:00:00", PREDICT_ACTIONS: "off" },
        },
      }),
    ],
    test: { setupFiles: ["./test/apply-migrations.ts"], testTimeout: 60_000 },
  };
});
