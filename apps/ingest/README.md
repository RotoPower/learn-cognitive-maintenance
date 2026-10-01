# plant-ingest (Cloudflare Worker)

Cron Worker (module D2.2). Every 10 real minutes (`*/10`, 2026-10-01; it was every minute) it reads `/tags/latest` from the plant
API and upserts one row per tag into the shared D1 table `readings`, keyed on
`(tag, ts)` so repeated runs within the same sim hour are no-ops. At clock speed
30 (sim seconds per real second: two real minutes = one sim hour) this yields a
continuous hourly history. Speed 3600 is one sim *hour per real second*.

If the sim clock jumped (dashboard "Jump +7 days") the gap is backfilled from
`/tags/{tag}/history`, up to `MAX_BACKFILL_HOURS` (default 168). With 82 tags a gap is filled `BACKFILL_TAGS_PER_PASS` (default 20) tags per cron pass, so a full 168 h takes 5 passes (50 minutes by cron; `scripts/prefill_staging.py` drives them by hand through `POST /ingest`): one pass may make at most 50 subrequests on the free plan (each history call and each D1 batch counts; one pass per tag failed with "Too many subrequests" on staging, 2026-10-01).

- `GET /health`: last ingested hour of the sentinel tag `TX1.MOISTURE_PPM` (open).
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
schema is migrated from there. Calls to the plant API go through a **Service Binding**
(`PLANT` -> `plant-api-staging`), not the public URL: a Worker fetching another Worker's
`workers.dev` address on the same account fails with Cloudflare error 1042. Locally
(no binding) the Worker falls back to `PLANT_API_URL`. Only the tokens are new:

```bash
wrangler secret put READ_TOKEN --env staging     # same value as the plant API's READ_TOKEN
wrangler secret put ADMIN_TOKEN --env staging
npm run deploy:staging
```

Then pause the plant clock; it moves only by demo jumps (budget below):
`uv run plantctl --url https://plant-api-staging.rotopower.workers.dev --admin speed --speed 0`,
and watch `https://plant-ingest-staging.rotopower.workers.dev/health` after a jump.

## D1 free-tier budget

Free D1 allows **5M rows read** and **100k rows written** per day (reset 00:00 UTC);
past either, every query fails with `D1_ERROR: ... exceeded D1's free tier daily ...`
until the reset. Staging hit the read limit on 2026-09-27 because each cron ran a
full-table `COUNT(*)`.

**Reads** depend on query shape, not cron frequency. Each pass now runs index-only
queries (~25-50 rows), about 70k reads/day at one pass a minute. Never `COUNT(*)` or
scan `readings` from a cron or an open route.

**Writes** depend on the clock speed, not the cron interval: a slower cron just
backfills the same hours in bigger batches. Each sim hour writes 82 tags (14 assets).
Migration 0005 dropped the `ix_readings_ts` index, which D1 counted as a second write per
row; every query now goes through the `(tag, ts)` primary key, using `TX1.MOISTURE_PPM` as
the sentinel tag for "is this hour stored". It is written last in each pass, so a pass that
fails halfway leaves the hour unmarked; and it is a full-plant tag, so hours stored by the
4-asset plant (24 tags, no sentinel) are rewritten with all 82 tags when the clock passes them.
A plant API without the sentinel is refused (every pass would otherwise rewrite a week).

| speed | sim hours / real day | D1 rows written / day (2 per reading) | two sim years take |
|---|---|---|---|
| 3600 | 86,400 | ~14M, limit gone in ~10 min | ~5 h |
| 60 | 1,440 | ~236k, over the limit | ~12 days |
| 30 | 720 | ~118k, **over the limit** | ~24 days |
| **0** (staging) | demo jumps only | ~27.6k per jump into a new week, 0 over a stored one | as demos reach it |

**Correction 2026-10-01:** D1 counts the primary-key index entry as a written row, so a reading costs 2, not 1
(measured: two new weeks raised `rows_written_24h` from 8.8k to 63.4k). Speed 30 is therefore over the free
tier as well; staging stays at speed 0.

A demo "Jump +7 days" backfills at most `MAX_BACKFILL_HOURS` (168 h). The scripted demo's
five jumps wrote 68,880 rows (~138k D1 writes) the first time and 0 on a repeat (rehearsal, 2026-09-29): staging runs
paused, the dashboard caps jumps into unstored weeks at `JUMP_NEW_GLOBAL_DAY` (2/day, ~55k writes), and the demo
weeks are pre-filled one week per day with `scripts/prefill_staging.py` so the demo itself writes nothing. When the clock is paused or
clamped at the horizon end, the current hour is already stored and a pass writes nothing.

## Deploys

Staging deploys on every push to `main` that touches this app (Workers Builds: tests, then
`npx wrangler deploy --env staging`); production only from a `v*` tag after approval. See
[docs/ci-cd.md](../../docs/ci-cd.md). D1 migrations are applied separately.
