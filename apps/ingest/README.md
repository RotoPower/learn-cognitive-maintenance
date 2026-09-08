# plant-ingest (Cloudflare Worker)

Cron Worker (module D2.2). Every real minute it reads `/tags/latest` from the plant
API and upserts one row per tag into the shared D1 table `readings`, keyed on
`(tag, ts)` so repeated runs within the same sim hour are no-ops. At clock speed
3600 (one real minute = one sim hour) this yields a continuous hourly history.

If the sim clock jumped (dashboard "Jump +7 days") the gap is backfilled from
`/tags/{tag}/history`, up to `MAX_BACKFILL_HOURS` (default 168).

- `GET /health`: last ingested hour, row and tag counts (open).
- `POST /ingest`: run one pass now (ADMIN token). The dashboard's demo controls use this.

## Local

```bash
npm install
cp .dev.vars.example .dev.vars
npm test
npm run dev              # then: curl "http://127.0.0.1:8787/__scheduled?cron=*+*+*+*+*"
```

## Staging

The D1 database is the one created for `apps/plant-api` (same `database_id`); its
schema is migrated from there. Only the tokens are new:

```bash
wrangler secret put READ_TOKEN --env staging     # same value as the plant API's READ_TOKEN
wrangler secret put ADMIN_TOKEN --env staging
npm run deploy:staging
```

Then set the plant clock to one sim hour per minute so rows accumulate:
`uv run plantctl --url https://plant-api-staging.rotopower.workers.dev --admin speed --speed 3600`,
and watch `https://plant-ingest-staging.rotopower.workers.dev/health`.
