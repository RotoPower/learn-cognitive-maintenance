"""CLI for the predict task. See ``python -m models.predict --help``."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd

from models.predict.features import build_features, load_long, load_repairs
from models.predict.model import (
    ARTIFACT_DIR, FEATURE_SETS, REPORTS_DIR, evaluate, load_artifact, predict_proba, save_artifact, score, select_features,
    train, upload,
)

DERIVED = Path("data/derived")


def _guard(path: Path) -> Path:
    if "raw" in path.parts:
        raise PermissionError("data/raw is read-only")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def cmd_features(a: argparse.Namespace) -> None:
    long = load_long(a.input)
    repairs = load_repairs(a.maintenance_log)
    feats = build_features(long, as_of=a.as_of, repairs=repairs, reset_at_repairs=a.reset_at_repairs)
    date = pd.Timestamp(a.as_of).strftime("%Y-%m-%d")
    out = _guard(Path(a.out) if a.out else DERIVED / f"predict_features_{date}.parquet")
    feats.to_parquet(out, index=False)
    print(f"wrote {out.as_posix()}: {feats.shape[0]} rows x {feats.shape[1]} cols, assets {sorted(feats['asset'].unique())}")


def cmd_train(a: argparse.Namespace) -> None:
    table = pd.read_parquet(a.table)
    cols = select_features(table, a.feature_set)
    art = train(table, horizon_days=a.horizon_days, seed=a.seed, cutoff=a.cutoff, l2=a.l2, run_id=a.run_id,
                persistence=a.persistence, features=cols)
    art["config"].update(feature_set=a.feature_set, reset_at_repairs=a.reset_at_repairs, table=Path(a.table).as_posix())
    path = save_artifact(art, Path(a.artifact_dir))
    te = art["metrics"]["test"] or {}
    print(f"run_id={art['run_id']} artifact={path.as_posix()}")
    print(json.dumps({k: te.get(k) for k in ("pr_auc", "precision_at_5", "lead_time_days_mean", "false_alerts_per_asset_month")}))


def cmd_score(a: argparse.Namespace) -> None:
    art = load_artifact(a.artifact, Path(a.artifact_dir))
    date = pd.Timestamp(a.as_of).strftime("%Y-%m-%d")
    fpath = Path(a.features) if a.features else DERIVED / f"predict_features_{date}.parquet"
    if art.get("config", {}).get("reset_at_repairs"):
        # A saved feature file cannot show whether its windows were reset at repairs, and an
        # unreset one scores this artefact wrongly (post-repair echoes). Build them here.
        if not (a.input and a.maintenance_log):
            raise SystemExit(f"{art['run_id']} needs features reset at repairs: pass --input and --maintenance-log "
                             "(saved --features files are not accepted for this artefact)")
        fpath = Path("__rebuild__")
    if not fpath.exists():
        if not a.input:
            raise SystemExit(f"{fpath} not found; pass --features or --input to build them")
        feats = build_features(load_long(a.input), as_of=a.as_of, repairs=load_repairs(a.maintenance_log),
                               reset_at_repairs=bool(art.get("config", {}).get("reset_at_repairs")))
    else:
        feats = pd.read_parquet(fpath)
    ranked = score(art, feats, a.as_of, report_dir=Path(a.report_dir))
    print(f"artifact={art['run_id']} as_of={a.as_of} report={(Path(a.report_dir) / f'predict_{date}.md').as_posix()}")
    print(ranked[["asset", "p_fail", "alert", "interpretation"]].to_string(index=False))


def cmd_evaluate(a: argparse.Namespace) -> None:
    """Score a saved artefact on any labelled table (e.g. a holdout year) with its own alert rule."""
    art = load_artifact(a.artifact, Path(a.artifact_dir))
    t = pd.read_parquet(a.table)
    t["timestamp"] = pd.to_datetime(t["timestamp"])
    t = t[t["label"].notna()]
    if a.after:
        t = t[t["timestamp"] > pd.Timestamp(a.after)]
    t = t.assign(p=predict_proba(art, t))
    cfg = art.get("config", {})
    m = evaluate(t, art["threshold"], int(cfg.get("step_hours", 24)), int(cfg.get("persistence", 1)))
    print(json.dumps({"artifact": art["run_id"], "table": Path(a.table).as_posix(), **m}, indent=2, default=str))


def cmd_upload(a: argparse.Namespace) -> None:
    token = a.token or os.environ.get("PLANT_ADMIN_TOKEN")
    if not token:
        raise SystemExit("set PLANT_ADMIN_TOKEN or pass --token")
    print(json.dumps(upload(a.artifact, a.url, token)))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="models.predict", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("features", help="build the lagged feature table")
    f.add_argument("--input", required=True, help="long parquet from the data agent, or wide sensors.csv")
    f.add_argument("--as-of", required=True)
    f.add_argument("--maintenance-log", default=None, help="JSON dump of GET /maintenance/log")
    f.add_argument("--out", default=None)
    f.add_argument("--reset-at-repairs", action="store_true", help="windows never cross a corrective repair")
    f.set_defaults(fn=cmd_features)

    t = sub.add_parser("train", help="fit logistic regression on a labelled table")
    t.add_argument("--table", required=True, help="parquet with features + label (from models.predict.labels)")
    t.add_argument("--horizon-days", type=int, default=30)
    t.add_argument("--cutoff", default=None, help="ISO timestamp; default = 70%% point of the table")
    t.add_argument("--seed", type=int, default=42)
    t.add_argument("--l2", type=float, default=1.0)
    t.add_argument("--persistence", type=int, default=1, help="alert after N consecutive daily scores above threshold")
    t.add_argument("--feature-set", choices=FEATURE_SETS, default="all")
    t.add_argument("--reset-at-repairs", action="store_true", help="record that the table's windows reset at repairs")
    t.add_argument("--run-id", default=None)
    t.add_argument("--artifact-dir", default=str(ARTIFACT_DIR))
    t.set_defaults(fn=cmd_train)

    s = sub.add_parser("score", help="rank assets by failure probability as of a timestamp")
    s.add_argument("--artifact", required=True, help="run id or path")
    s.add_argument("--as-of", required=True)
    s.add_argument("--features", default=None, help="feature parquet; default data/derived/predict_features_<date>.parquet")
    s.add_argument("--input", default=None, help="long table to build features from if --features is absent")
    s.add_argument("--maintenance-log", default=None)
    s.add_argument("--artifact-dir", default=str(ARTIFACT_DIR))
    s.add_argument("--report-dir", default=str(REPORTS_DIR))
    s.set_defaults(fn=cmd_score)

    e = sub.add_parser("evaluate", help="metrics of a saved artefact on a labelled table (holdout)")
    e.add_argument("--artifact", required=True)
    e.add_argument("--table", required=True)
    e.add_argument("--after", default=None, help="only rows after this timestamp")
    e.add_argument("--artifact-dir", default=str(ARTIFACT_DIR))
    e.set_defaults(fn=cmd_evaluate)

    u = sub.add_parser("upload", help="POST an artefact to /admin/model_artifacts")
    u.add_argument("--artifact", required=True)
    u.add_argument("--url", default="http://127.0.0.1:8000")
    u.add_argument("--token", default=None)
    u.set_defaults(fn=cmd_upload)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
