"""Failure-within-horizon prediction (predict skill).

    from models.predict import build_features, train, score, upload

Pipeline: features (anyone) -> labels (validator only, models.predict.labels)
-> train (Colab or local) -> upload artefact -> score via the CLI:

    uv run python -m models.predict features --input data/derived/anomaly_fleet_2024-09-20.parquet --as-of 2024-09-20
    uv run python -m models.predict.labels   --features ... --ground-truth data/sim/ground_truth.json --out ...
    uv run python -m models.predict train    --table ... --horizon-days 30 --cutoff 2024-08-20
    uv run python -m models.predict score    --artifact <run_id> --as-of 2024-09-20
"""

from models.predict.features import build_features, feature_columns, load_long, load_repairs
from models.predict.model import load_artifact, predict_proba, save_artifact, score, train, upload

__all__ = [
    "build_features",
    "feature_columns",
    "load_long",
    "load_repairs",
    "load_artifact",
    "predict_proba",
    "save_artifact",
    "score",
    "train",
    "upload",
]
