"""Label builder for the predict task. VALIDATOR-APPROVED SCRIPT.

This is the only code allowed to touch ground truth on the modelling side.
It attaches ``label`` to a feature table and nothing else: no failure times,
no modes, no health. A row (asset, t) gets

    label = 1  if a scripted failure of that asset occurs in (t, t + horizon]
    label = 0  otherwise
    label = NaN if t + horizon is after the last observed timestamp
               (the outcome is not knowable yet; such rows are never trained on)

Sources of failures, in order of preference:
  --ground-truth data/sim/ground_truth.json           (the simulator sidecar)
  --api-url http://host:8000 with $PLANT_ADMIN_TOKEN   (GET /admin/ground_truth)

Usage
-----
    uv run python -m models.predict.labels --features data/derived/predict_features_2024-09-20.parquet \
        --horizon-days 30 --ground-truth data/sim/ground_truth.json \
        --out data/derived/predict_fleet_2024-09-20.parquet
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd


def load_failures(ground_truth: str | Path | dict | None = None, api_url: str | None = None) -> pd.DataFrame:
    """-> DataFrame[asset, failure] from a ground_truth.json or the admin API."""
    if ground_truth is not None:
        gt = ground_truth if isinstance(ground_truth, dict) else json.loads(Path(ground_truth).read_text(encoding="utf-8"))
    elif api_url is not None:
        token = os.environ.get("PLANT_ADMIN_TOKEN")
        if not token:
            raise RuntimeError("PLANT_ADMIN_TOKEN is not set")
        req = urllib.request.Request(
            api_url.rstrip("/") + "/admin/ground_truth",
            headers={"Authorization": f"Bearer {token}", "User-Agent": "plant-models/0.1 (+https://github.com/RotoPower/learn-cognitive-maintenance)"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            gt = json.loads(r.read().decode())
    else:
        raise ValueError("give ground_truth or api_url")
    rows = [{"asset": f["asset"], "failure": pd.Timestamp(f["failure"])} for f in gt["failures"]]
    return pd.DataFrame(rows, columns=["asset", "failure"])


def make_labels(
    features: pd.DataFrame,
    failures: pd.DataFrame,
    horizon_days: int,
    known_until: str | pd.Timestamp | None = None,
) -> pd.Series:
    """Label series aligned to ``features`` rows (float: 0.0, 1.0 or NaN)."""
    ts = pd.to_datetime(features["timestamp"])
    horizon = pd.Timedelta(days=horizon_days)
    known_until = pd.Timestamp(known_until) if known_until is not None else ts.max()
    label = np.zeros(len(features), dtype=float)
    for asset, g in failures.groupby("asset"):
        m_asset = (features["asset"] == asset).to_numpy()
        for f in g["failure"]:
            hit = m_asset & (ts.to_numpy() < np.datetime64(f)) & (np.datetime64(f) <= (ts + horizon).to_numpy())
            label[hit] = 1.0
    unknown = (ts + horizon).to_numpy() > np.datetime64(known_until)
    label[unknown] = np.nan
    return pd.Series(label, index=features.index, name="label")


def attach_labels(features: pd.DataFrame, failures: pd.DataFrame, horizon_days: int, known_until=None) -> pd.DataFrame:
    out = features.copy()
    out["label"] = make_labels(features, failures, horizon_days, known_until)
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="models.predict.labels", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--features", required=True, help="feature parquet from `models.predict features`")
    p.add_argument("--horizon-days", type=int, default=30)
    p.add_argument("--ground-truth", default=None, help="path to ground_truth.json")
    p.add_argument("--api-url", default=None, help="plant API base url (needs PLANT_ADMIN_TOKEN)")
    p.add_argument("--out", required=True, help="output parquet: features + label")
    a = p.parse_args(argv)

    feats = pd.read_parquet(a.features)
    failures = load_failures(a.ground_truth, a.api_url)
    table = attach_labels(feats, failures, a.horizon_days)
    out = Path(a.out)
    if "raw" in out.parts:
        raise PermissionError("data/raw is read-only")
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(out, index=False)
    lab = table["label"]
    print(
        f"wrote {out.as_posix()}: {len(table)} rows, horizon {a.horizon_days}d, "
        f"positives {int((lab == 1).sum())}, negatives {int((lab == 0).sum())}, unknown {int(lab.isna().sum())}"
    )


if __name__ == "__main__":
    main()
