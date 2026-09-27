# plant-dashboard (Cloudflare Worker + static assets)

The public management view (module D3). No login: anyone with the link can use every
demo feature, so abuse is controlled by limits, not accounts.

- `public/`: one page, plain HTML/CSS/JS, no build step. KPI strip, fleet board, asset
  drawer (prediction, recommended actions, 30-day trends as one small chart per tag,
  alert periods shaded), alerts and work-order tables with an asset filter, demo controls.
- `src/index.ts`: answers `/api/*` only. It calls the plant API and the scoring Worker over
  Service Bindings with tokens it holds as secrets, so **no token reaches the browser**.
  Only an allow-list of routes exists; ground truth is never proxied.

| route | what |
|---|---|
| `GET /api/overview` | sim time, KPIs, fleet board, alerts, work orders |
| `GET /api/asset/:id` | trends (from the plant API), alerts, prediction, actions, work orders |
| `POST /api/workorder` | draft, then `{confirm: true}` stores it with `source='demo'` |
| `POST /api/demo/:action` | `jump7`, `jump {day}`, `score`, `inject {asset_id}`, `reset` |

## Limits and D1 budget

- 60 API requests per minute per viewer (`API_LIMITER` rate-limit binding).
- Demo actions: 5 per hour per viewer, 60 per hour for everyone; work orders 10 per hour
  per viewer. Logged in D1 `demo_actions` with a hashed viewer id, never the IP.
- *Run scoring now*: 30 per day in total. Each real pass reads ~35k rows (see
  `apps/scoring/README.md`), so this cap keeps the demo inside the free 5M reads/day.
- The clock only moves forward inside the horizon; *Reset* restores the scripted plant
  and clears scoring outputs (readings stay: they are identical for the scripted plant;
  readings written while an injected fault was active are not rewritten).
- Overview and asset responses are cached per isolate for `CACHE_SECONDS` (30). Trends come
  from the plant API, which computes them from the simulator, so they cost no D1 reads.
- Risk % is shown with an "experimental model" label and does not set asset status
  while `PREDICT_TRUSTED=off` (the predict model is NO-GO). Status comes from anomaly alerts.

## Local, end to end

All four Workers share one local D1 through `--persist-to`:

```bash
cd apps/plant-api && npx wrangler d1 migrations apply plant-local --local --persist-to ../.wrangler-shared
# one terminal each (copy .dev.vars.example to .dev.vars in each app first)
cd apps/plant-api && npx wrangler dev --port 8787 --inspector-port 9230 --persist-to ../.wrangler-shared
cd apps/ingest    && npx wrangler dev --port 8788 --inspector-port 9231 --persist-to ../.wrangler-shared --var MAX_BACKFILL_HOURS:1680 --var INITIAL_BACKFILL_HOURS:1680
cd apps/scoring   && npx wrangler dev --port 8789 --inspector-port 9232 --persist-to ../.wrangler-shared
cd apps/dashboard && npx wrangler dev --port 8790 --inspector-port 9233 --persist-to ../.wrangler-shared
```

Then seed: jump the clock (`POST :8787/clock/jump {"to":"2024-09-20T13:00:00"}`, admin
token), `POST :8788/ingest` (local dev runs no crons; after each demo jump, call it again),
upload artefacts with `PLANT_ADMIN_TOKEN=admin-token uv run plantctl --url http://127.0.0.1:8787 --admin upload-artifact --file ...`,
`POST :8789/score?force=1`, and open http://127.0.0.1:8790.

## Staging

Apply the plant-api migrations first (0003 adds `demo_actions`), then:

```bash
wrangler secret put READ_TOKEN --env staging
wrangler secret put ADMIN_TOKEN --env staging            # plant API admin token
wrangler secret put SCORING_ADMIN_TOKEN --env staging    # only if scoring uses a different one
npm run deploy:staging
```

`npm test` covers the API (fake plant and scoring bindings): KPIs as of sim time, future
alerts hidden, draft/confirm work orders, forward-only jumps, per-viewer and daily caps,
hashed viewer ids, and that no response ever contains a token.
