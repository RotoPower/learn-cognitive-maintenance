# learn-cognitive-maintenance — cognitive maintenance MVP
## Environment
- Python via `uv run`; tests via `uv run pytest -q`
- Windows native; use forward-slash paths
## Data rules
- `data/raw/` is read-only. Derived data goes to `data/derived/`.
- Ground truth (`failures`, hidden health) is never used as a model input. Only the validator may read it.
- Tag naming: `<ASSET>.<TAG>`, e.g. `GT1.EXH_TEMP`. Asset list in docs/plant.md.
## Reproducibility
- Every training run has a seed and a run id; artefacts are JSON in `models/artifacts/`.
## Never
- No `pip install` — use `uv add`. Never commit notebooks with outputs. Never write to `data/raw/`.