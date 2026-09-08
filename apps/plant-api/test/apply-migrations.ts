import { applyD1Migrations, env } from "cloudflare:test";

// Runs once per test file before tests: create the D1 schema in the Miniflare database.
await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);
