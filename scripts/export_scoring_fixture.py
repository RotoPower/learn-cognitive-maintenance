"""Write a parity fixture from the Python scorers for the scoring Worker (D2.4).

apps/scoring/test/fixtures/scoring.json holds the hourly readings the Worker would
read from D1 (LOOKBACK_DAYS ending at AS_OF, from the simulator), the corrective
repairs visible at AS_OF, and what the Python code computes from exactly those
readings: the predict feature row per asset (models.predict.features), the
probability and drivers from the committed predict artefact, and the anomaly flags
(models.anomaly). The TypeScript port must reproduce them.

    uv run python scripts/export_scoring_fixture.py

Never reads ground truth: repairs are the maintenance log (what GET
/maintenance/log shows at AS_OF), not the failure table.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from models import anomaly as A
from models.predict import build_features, load_artifact, predict_proba
from models.predict.features import _hours_since_repair
from models.predict.model import _drivers, interpret
from plant.sim import TAGS, Plant

OUT = Path("apps/scoring/test/fixtures/scoring.json")
AS_OF = pd.Timestamp("2024-09-20T13:00:00")  # not midnight: the feature row is the day's 00:00
LOOKBACK_DAYS = 60
DATA_START = pd.Timestamp("2024-01-01")  # plant horizon start; hours_since_repair origin in training
ARTIFACT = "predict_fleet_h30_2024-10-15_s42"


def _r(v: float) -> float | None:
    """Round to 10 significant digits so the fixture is compact; the expectations
    below are computed from the rounded values, so parity is exact in intent."""
    return None if math.isnan(v) else float(f"{v:.10g}")


def main() -> None:
    plant = Plant.from_yaml(seed=42)
    hours = pd.date_range(AS_OF - pd.Timedelta(days=LOOKBACK_DAYS) + pd.Timedelta(hours=1), AS_OF, freq="1h")
    readings: dict[str, list[float | None]] = {}
    rows = []
    for asset, tags in TAGS.items():
        for tag in tags:
            full = f"{asset}.{tag}"
            vals = [_r(plant.value(asset, tag, t.to_pydatetime())) for t in hours]
            readings[full] = vals
            rows += [{"timestamp": t, "asset": asset, "tag": full, "value": np.nan if v is None else v} for t, v in zip(hours, vals)]
    long = pd.DataFrame(rows)
    const = long.groupby("tag")["value"].nunique(dropna=True)
    long["dead"] = long["tag"].map(const <= 1).astype(bool)

    repairs_log = [
        {"kind": "corrective_repair", "asset_id": f["asset"], "timestamp": pd.Timestamp(f["repair"]).isoformat()}
        for f in plant.failures()
        if pd.Timestamp(f["repair"]) <= AS_OF
    ]
    repairs = pd.DataFrame(
        [{"asset": e["asset_id"], "timestamp": pd.Timestamp(e["timestamp"])} for e in repairs_log],
        columns=["asset", "timestamp"],
    )

    # predict: the latest daily feature row at or before AS_OF, per asset
    feats = build_features(long, as_of=AS_OF, repairs=repairs, step_hours=24)
    art = load_artifact(ARTIFACT)
    latest = feats[feats["timestamp"] <= AS_OF].sort_values("timestamp").groupby("asset").tail(1).copy()
    grid = pd.DatetimeIndex(latest["timestamp"])
    latest["hours_since_repair"] = [
        _hours_since_repair(a, pd.DatetimeIndex([t]), repairs, DATA_START)[0] for a, t in zip(latest["asset"], grid)
    ]
    latest["p_fail"] = predict_proba(art, latest)
    predict = []
    for _, r in latest.sort_values("asset").iterrows():
        d = _drivers(art, r)
        predict.append(
            {
                "asset": r["asset"],
                "features_at": r["timestamp"].isoformat(),
                "features": {c: _r(float(r[c])) if c in r and not pd.isna(r[c]) else None for c in art["feature_names"]},
                "p_fail": float(r["p_fail"]),
                "drivers": [[n, c] for n, c in d],
                "interpretation": interpret(d),
            }
        )

    # anomaly: flags from the same readings
    cfg = A.Config()
    flags, _ = A.score_table(long, {}, AS_OF, cfg)
    anomaly = [
        {
            "asset": f["asset"],
            "tag": f["tag"],
            "first_flag_ts": f["first_flag_ts"].isoformat(),
            "last_flag_ts": f["last_flag_ts"].isoformat(),
            "hours_flagged": f["hours_flagged"],
            "severity": f["severity"],
            "z_peak_signed": f["z_peak_signed"],
            "z_at_end": f["z_at_end"],
            "interpretation": f["interpretation"],
        }
        for f in flags
    ]

    fixture = {
        "as_of": AS_OF.isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "data_start": DATA_START.isoformat(),
        "first_hour": hours[0].isoformat(),
        "artifact": {k: art[k] for k in ("run_id", "feature_names", "scaler", "coefficients", "intercept", "threshold", "horizon_days")},
        "anomaly_config": {k: getattr(cfg, k) for k in ("baseline_days", "min_periods_hours", "z_threshold", "min_run_hours", "window_days", "target_assets")},
        "repairs": repairs_log,
        "readings": readings,
        "expected": {"predict": predict, "anomaly": anomaly},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixture, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT} {OUT.stat().st_size // 1024} KB; predict {[(p['asset'], round(p['p_fail'], 3)) for p in predict]}; "
          f"anomaly {[(f['tag'], f['hours_flagged']) for f in anomaly]}")


if __name__ == "__main__":
    main()
