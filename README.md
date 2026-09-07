# learn-cognitive-maintenance

Cognitive maintenance MVP. See `CLAUDE.md` for project rules and `docs/plant.md`
for the plant model (assets, tags, fault modes).

## Simulator

`plant/sim.py` is a deterministic simulator: every sensor value is a pure
function of `(seed, asset, tag, sim_time)`. The failure script lives in
`plant/faults.yaml`.

```bash
uv run python -m plant.sim --out data/sim
```

writes `data/sim/sensors.csv` (hourly, one column per tag) and
`data/sim/ground_truth.json` (validator only; never a model input).

```python
from plant import sim
sim.value(42, "GT1", "EXH_TEMP", "2024-10-15 14:00")   # one reading
sim.plant(42).generate()                                # full DataFrame
sim.failures(42)                                        # ground truth
```

The generated table deliberately contains a dead (constant) tag, a block of
missing hours, one duplicated timestamp and a three-day sensor outage.

## Tests

```bash
uv run pytest -q
```

## API

`plant/api.py` is a FastAPI front end over the simulator. The simulated clock
is the only essential state; every reading is recomputed from
`(seed, asset, tag, sim_time)` per request.

```bash
uv run uvicorn plant.api:app --reload
```

Tokens are bearer tokens (`Authorization: Bearer ...` or `X-API-Key`), taken
from `PLANT_READ_TOKEN` and `PLANT_ADMIN_TOKEN` (dev defaults `read-token` /
`admin-token`; set real values before exposing the server). `PLANT_CLOCK_START`
optionally sets the initial sim time.

| Route | Token | Notes |
|---|---|---|
| `GET /clock` | READ | `{"sim_time", "speed"}` |
| `POST /clock/speed {"speed": 3600}` | ADMIN | sim seconds per real second; 0 pauses |
| `POST /clock/jump {"to": "2024-09-01T00:00"}` | ADMIN | within the horizon |
| `GET /assets` | READ | ids, descriptions, tags |
| `GET /tags/{tag}/history?from=&to=&interval=1h` | READ | `to` is clamped to sim now; outage -> `null` |
| `GET /tags/latest?asset_id=` | READ | top-of-hour scan at sim now |
| `GET /maintenance/log?asset_id=` | READ | past corrective repairs + work orders |
| `POST /maintenance/workorder` | READ | `{"asset_id", "type", "description"}` |
| `GET /admin/ground_truth` | ADMIN | failures, events, current health |
| `POST /admin/inject_fault` | ADMIN | `{"asset", "mode", "onset", "duration_days"}` |
| `POST /admin/reset {"seed": 42}` | ADMIN | new seed, clears injected faults/work orders, clock to start |
| `POST /admin/model_artifacts` | ADMIN | saves `models/artifacts/<run_id>.json` |
