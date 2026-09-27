# plant-scoring (Cloudflare Worker)

Cron Worker (module D2.4). Once per sim day it scores the fleet from D1 and writes
the results back to D1 for the dashboard:

- **anomaly**: 30-day rolling z-scores per tag (window closed on the left), a flag for
  every run of >= 6 h with |z| > 3, rolled up into episodes (runs of one tag < 24 h apart)
  and written to `alerts` (an open alert is extended, not duplicated). Every asset the
  plant API lists (`/assets`, 14 in the full plant) is scored; thresholds come from the
  latest `anomaly` artefact in `model_artifacts`. Each flag is read against the symptom
  map of the asset's family (GT1 and GT2 share `GT`), the same map as `models/anomaly.py`.
- **predict**: for the assets the model was validated on (`PREDICT_ASSETS`: GT1, BFP1,
  BFP2, CTF1; the new assets follow in roadmap Phase 3), the daily feature row per asset (current, 7-day mean, 7- and 30-day
  slopes, load, hours since repair), standardised with the latest `predict` artefact,
  logistic probability and top drivers, written to `predictions`. With
  `PREDICT_ACTIONS=on`, a probability at or above the artefact's threshold also opens a
  `predict` alert and an inspection work order in `maintenance_log` (`source='scoring'`).
  On in staging since the validator's GO (`PREDICT_ACTIONS=on`); off in production.

Both scorers are ports of the Python code (`models/anomaly.py`, `models/predict/`);
`test/score.test.ts` checks them against `test/fixtures/scoring.json`, regenerated with
`uv run python scripts/export_scoring_fixture.py`.

- `GET /health`: last run (open, one indexed row).
- `POST /score[?force=1]`: score now (ADMIN token). Without `force`, an already-scored
  sim day is a no-op. The dashboard's *Run scoring now* uses this.

## Cadence and D1 budget

The cron is `0 * * * *`: at clock speed 30 one real hour is half a sim day, so every
other pass scores a new sim day. A pass reads `LOOKBACK_DAYS` (37) of hourly readings per
tag through the `(tag, ts)` primary key: 82 tags x 888 h, about **73k rows read**. The
37 days cover the anomaly baseline (30 days, 20-day minimum) plus the 7-day window and
the predict model's 7-day slopes. At 24 passes a day, with half of them no-ops, that is
~0.9-1.75M of the free 5M. A pass for a sim day already scored reads one row, so a paused
or clamped clock is free. Writes are a few dozen rows per pass. Keep `POST /score?force=1`
for demos: each forced pass costs another ~73k reads.

## Local

```bash
npm install
cp .dev.vars.example .dev.vars
npm test
npm run dev              # then: curl "http://127.0.0.1:8787/__scheduled?cron=0+*+*+*+*"
```

## Staging

Same D1 database as `apps/plant-api`. Apply its migrations first (0002 adds the indexes
this Worker uses), then set the tokens and deploy:

```bash
cd ../plant-api && npx wrangler d1 migrations apply plant-staging --env staging --remote
cd ../scoring
wrangler secret put READ_TOKEN --env staging     # same value as the plant API's READ_TOKEN
wrangler secret put ADMIN_TOKEN --env staging
npm run deploy:staging
```

Check with `curl -X POST -H "Authorization: Bearer $ADMIN" https://plant-scoring-staging.rotopower.workers.dev/score`
and `GET /health`.

## Deploys

Staging deploys on every push to `main` that touches this app (Workers Builds: tests, then
`npx wrangler deploy --env staging`); production only from a `v*` tag after approval. See
[docs/ci-cd.md](../../docs/ci-cd.md). D1 migrations are applied separately.
