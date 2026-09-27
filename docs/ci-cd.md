# CI/CD (module D2.6)

| | trigger | runs | where the token lives |
|---|---|---|---|
| **staging** | push to `main` | Workers Builds, one build per Worker | Workers Builds' own build token (Cloudflare) |
| **production** | a `v*` git tag + one human approval | `.github/workflows/production.yml` | GitHub Environment `production` secrets |

Nothing deploys production from a PC: `scripts/block-prod-deploy.sh` blocks it in Claude
Code, and the only production credential is a GitHub Environment secret.

## Staging: Workers Builds

Workers Builds has no tag trigger, so it does staging only. In the Cloudflare dashboard,
for each **staging** Worker: *Workers & Pages -> the Worker -> Settings -> Build ->
Connect*, pick GitHub `RotoPower/learn-cognitive-maintenance`, then:

| Worker | Root directory | Build command | Deploy command | Build watch paths (include) |
|---|---|---|---|---|
| `plant-api-staging` | `apps/plant-api` | `npm ci && npm test` | `npx wrangler deploy --env staging` | `apps/plant-api/*` |
| `plant-ingest-staging` | `apps/ingest` | `npm ci && npm test` | `npx wrangler deploy --env staging` | `apps/ingest/*`, `apps/plant-api/migrations/*` |
| `plant-scoring-staging` | `apps/scoring` | `npm ci && npm test` | `npx wrangler deploy --env staging` | `apps/scoring/*`, `apps/plant-api/migrations/*` |
| `plant-dashboard-staging` | `apps/dashboard` | `npm ci && npm test` | `npx wrangler deploy --env staging` | `apps/dashboard/*`, `apps/plant-api/migrations/*` |

- Branch: `main`. Non-production branch builds: off, or preview command
  `npx wrangler versions upload --env staging` (uploads a version, does not deploy it).
- The ingest, scoring and dashboard tests apply `apps/plant-api/migrations`, hence the
  extra watch path.
- A failing `npm test` fails the build and nothing is deployed.
- **D1 migrations are not applied by the build.** A change under
  `apps/plant-api/migrations/` is applied by hand or by the `deployer` agent before the
  push that needs it: `cd apps/plant-api && npx wrangler d1 migrations apply plant-staging --env staging --remote`.
- Secrets (`READ_TOKEN`, `ADMIN_TOKEN`, ...) stay as set with `wrangler secret put`;
  builds do not touch them.

## Production: GitHub Actions on a tag

One-time setup, all by a human:

1. Create production: `wrangler d1 create plant-production`, put its id in the four
   `apps/*/wrangler.toml` `[env.production]` blocks (the workflow refuses to run while
   `REPLACE_WITH_OUTPUT_OF` is still there). Set each production Worker's secrets with
   `wrangler secret put ... --env production` (needs `.approvals/prod-<sha>` when run
   from Claude Code; simplest from your own terminal).
2. Cloudflare API token: *My Profile -> API Tokens -> Create*, template "Edit Cloudflare
   Workers" plus **D1: Edit**, scoped to this account.
3. GitHub: *Settings -> Environments -> New environment* `production`, add yourself as
   **Required reviewer**, restrict deployment to tags `v*`, and add environment secrets
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`. With the CLI:
   `gh secret set CLOUDFLARE_API_TOKEN --env production` (prompts; the value never
   touches a file).

Release: `git tag v0.1.0 && git push origin v0.1.0`. The `test` job runs every Python and
Worker test without secrets; then the `deploy` job waits for your approval and deploys
plant-api (after migrating D1), ingest, scoring, dashboard, in that order.
