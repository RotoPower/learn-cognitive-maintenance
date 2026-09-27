"""Write a parity fixture from the Python scorers for the scoring Worker (D2.4).

apps/scoring/test/fixtures/scoring.json holds hourly readings from the simulator (the
union of every case's LOOKBACK_DAYS window), the corrective repairs of the CMMS log, the
predict artefact, and per case (an as-of time) what the Python code computes from
exactly the readings the Worker would read: the predict feature row per asset
(models.predict.features, windows reset at repairs), the probability, the 2-day
persistent alert and drivers (models.predict.model), and the anomaly flags
(models.anomaly). The TypeScript port must reproduce them.

Cases: 2024-09-20 13:00 (BFP2 degrading) and 2024-10-03 13:00 (six days after the BFP2
repair, so the reset windows matter). One anomaly-only extra case, 2024-07-17 13:00 (HRSG1
tube leak), carries its own readings for the ten assets added with the full plant, so the
family symptom map is checked on a new mode too. `symptom_map` is models.anomaly's
FAILURE_MODES; the Worker's copy must equal it.

    uv run python scripts/export_scoring_fixture.py

Never reads ground truth: repairs are the CMMS export (Plant.maintenance_log), the same
entries GET /maintenance/log shows.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from models import anomaly as A
from models.predict import build_features, load_artifact, load_repairs
from models.predict.model import _drivers, alert_mask, interpret, predict_proba
from plant.sim import TAGS, Plant

ORIGINAL_ASSETS = ("GT1", "BFP1", "BFP2", "CTF1")

OUT = Path("apps/scoring/test/fixtures/scoring.json")
CASES = [pd.Timestamp("2024-09-20T13:00:00"), pd.Timestamp("2024-10-03T13:00:00")]
EXTRA_ANOMALY_CASES = [pd.Timestamp("2024-07-17T13:00:00")]
LOOKBACK_DAYS = 37  # apps/scoring/wrangler.toml
ARTIFACT = "predict_fleet_h30_2024-01-01_s42_sym7"


def _r(v: float) -> float | None:
    """10 significant digits keep the fixture compact; expectations use the rounded values."""
    return None if math.isnan(v) else float(f"{v:.10g}")


def _readings(plant: Plant, hours: pd.DatetimeIndex, assets) -> tuple[dict[str, list[float | None]], pd.DataFrame]:
    readings: dict[str, list[float | None]] = {}
    rows = []
    for asset in assets:
        for tag in TAGS[asset]:
            full = f"{asset}.{tag}"
            vals = [_r(plant.value(asset, tag, t.to_pydatetime())) for t in hours]
            readings[full] = vals
            rows += [{"timestamp": t, "asset": asset, "tag": full, "value": np.nan if v is None else v} for t, v in zip(hours, vals)]
    return readings, pd.DataFrame(rows)


def _window(union: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    window = union[(union["timestamp"] > as_of - pd.Timedelta(days=LOOKBACK_DAYS)) & (union["timestamp"] <= as_of)].copy()
    const = window.groupby("tag")["value"].nunique(dropna=True)
    window["dead"] = window["tag"].map(const <= 1).astype(bool)
    return window


def _anomaly(window: pd.DataFrame, as_of: pd.Timestamp, cfg: A.Config) -> list[dict]:
    flags, _ = A.score_table(window, {}, as_of, cfg)
    return [{
        "asset": f["asset"], "tag": f["tag"],
        "first_flag_ts": f["first_flag_ts"].isoformat(), "last_flag_ts": f["last_flag_ts"].isoformat(),
        "hours_flagged": f["hours_flagged"], "severity": f["severity"],
        "z_peak_signed": f["z_peak_signed"], "z_at_end": f["z_at_end"], "interpretation": f["interpretation"],
    } for f in flags]


def main() -> None:
    plant = Plant.from_yaml(seed=42)
    first = CASES[0] - pd.Timedelta(days=LOOKBACK_DAYS) + pd.Timedelta(hours=1)
    hours = pd.date_range(first, CASES[-1], freq="1h")
    readings, union = _readings(plant, hours, TAGS)
    log = [e for e in plant.maintenance_log() if pd.Timestamp(e["timestamp"]) <= CASES[-1]]
    art = load_artifact(ARTIFACT)
    persistence = int(art["config"].get("persistence", 1))
    cfg = A.Config()

    cases = []
    for as_of in CASES:
        window = _window(union, as_of)
        repairs = load_repairs([e for e in log if pd.Timestamp(e["timestamp"]) <= as_of])

        feats = build_features(window, as_of=as_of, repairs=repairs, step_hours=24, reset_at_repairs=bool(art["config"].get("reset_at_repairs")))
        recent = feats[feats["timestamp"] <= as_of].sort_values("timestamp").groupby("asset").tail(persistence).copy()
        recent["p"] = predict_proba(art, recent)
        recent["alert"] = alert_mask(recent, art["threshold"], persistence)
        predict = []
        for _, r in recent.groupby("asset").tail(1).sort_values("asset").iterrows():
            d = _drivers(art, r)
            prev = recent[(recent["asset"] == r["asset"]) & (recent["timestamp"] < r["timestamp"])]
            predict.append({
                "asset": r["asset"],
                "features_at": r["timestamp"].isoformat(),
                "features": {c: _r(float(r[c])) if not pd.isna(r[c]) else None for c in art["feature_names"]},
                "p_fail": float(r["p"]),
                "p_prev": [float(x) for x in prev["p"]],
                "alert": bool(r["alert"]),
                "drivers": [[n, c] for n, c in d],
                "interpretation": interpret(d),
            })

        cases.append({"as_of": as_of.isoformat(), "expected": {"predict": predict, "anomaly": _anomaly(window, as_of, cfg)}})

    extra = []
    new_assets = [a for a in TAGS if a not in ORIGINAL_ASSETS]
    for as_of in EXTRA_ANOMALY_CASES:
        h = pd.date_range(as_of - pd.Timedelta(days=LOOKBACK_DAYS) + pd.Timedelta(hours=1), as_of, freq="1h")
        r, df = _readings(plant, h, new_assets)
        extra.append({"as_of": as_of.isoformat(), "first_hour": h[0].isoformat(), "readings": r,
                      "expected": {"anomaly": _anomaly(_window(df, as_of), as_of, A.Config(target_assets=new_assets))}})

    fixture = {
        "lookback_days": LOOKBACK_DAYS,
        "first_hour": hours[0].isoformat(),
        "artifact": {**{k: art[k] for k in ("run_id", "feature_names", "scaler", "coefficients", "intercept", "threshold", "horizon_days")}, "config": art["config"]},
        "anomaly_config": {k: getattr(cfg, k) for k in ("baseline_days", "min_periods_hours", "z_threshold", "min_run_hours", "window_days", "target_assets")},
        "repairs": log,
        "readings": readings,
        "cases": cases,
        "extra_anomaly_cases": extra,
        "symptom_map": {f"{fam}.{tag}": [list(m) for m in modes] for (fam, tag), modes in A.FAILURE_MODES.items()},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixture, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT} {OUT.stat().st_size // 1024} KB")
    for c in cases:
        print(" ", c["as_of"], "predict", [(p["asset"], round(p["p_fail"], 3), p["alert"]) for p in c["expected"]["predict"]],
              "anomaly", [(f["tag"], f["hours_flagged"]) for f in c["expected"]["anomaly"]])
    for c in extra:
        print(" ", c["as_of"], "anomaly (new assets)", [(f["tag"], f["hours_flagged"]) for f in c["expected"]["anomaly"]])


if __name__ == "__main__":
    main()
