"""Prepare the predict training/test table from two simulated years.

The 2024 demo plant has one failure per mode, too few to learn from and to test on at
once. So the model trains on the fleet's previous year (plant/faults_history_2023.yaml,
seven failures, all three modes) and is tested on the whole of 2024: every 2024 failure
is out of sample and the split stays strictly time based (cutoff 2024-01-01).

    # 1. features per year (sensors only; never reads ground truth)
    uv run python scripts/prepare_predict_table.py features --sensors data/sim/history_2023/sensors.csv --maintenance-log data/sim/history_2023/maintenance_log.json --out data/derived/predict_features_2023.parquet
    uv run python scripts/prepare_predict_table.py features --sensors data/sim/sensors.csv --maintenance-log data/sim/maintenance_log.json --out data/derived/predict_features_2024.parquet
    # 2. labels per year: validator-approved script, the only one that reads ground truth
    uv run python -m models.predict.labels --features data/derived/predict_features_2023.parquet --ground-truth data/sim/history_2023/ground_truth.json --horizon-days 30 --out data/derived/predict_labelled_2023.parquet
    uv run python -m models.predict.labels --features data/derived/predict_features_2024.parquet --ground-truth data/sim/ground_truth.json --horizon-days 30 --out data/derived/predict_labelled_2024.parquet
    # 3. one table
    uv run python scripts/prepare_predict_table.py combine data/derived/predict_labelled_2023.parquet data/derived/predict_labelled_2024.parquet --out data/derived/predict_fleet_2023-2024.parquet

Features: models.predict.build_features (current, 7-day mean, 7/30-day slopes, load),
with windows reset at every corrective repair in the CMMS export (maintenance_log.json,
operational data, not ground truth), minus ``hours_since_repair``: the validator found it acting as a clock proxy (it
memorised the single training failure), and the scoring Worker does not need it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from models.predict import build_features, load_long, load_repairs

DROP = ["hours_since_repair"]


def _out(path: str) -> Path:
    out = Path(path)
    if "raw" in out.parts:
        raise PermissionError("data/raw is read-only (CLAUDE.md)")
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def features(sensors: str, out: str, maintenance_log: str | None = None) -> pd.DataFrame:
    long = load_long(sensors)
    repairs = load_repairs(maintenance_log)
    feats = build_features(long, repairs=repairs, step_hours=24, reset_at_repairs=True).drop(columns=DROP, errors="ignore")
    feats.to_parquet(_out(out), index=False)
    print(f"wrote {out}: {len(feats)} rows, {feats['timestamp'].min().date()}..{feats['timestamp'].max().date()}, "
          f"{len(feats.columns) - 2} feature columns, dead tags {sorted(long.loc[long['dead'], 'tag'].unique())}")
    return feats


def combine(paths: list[str], out: str) -> pd.DataFrame:
    parts = [pd.read_parquet(p) for p in paths]
    for p, t in zip(paths, parts):
        if "label" not in t.columns:
            raise ValueError(f"{p} has no label column: run models.predict.labels on it first")
    table = pd.concat(parts, ignore_index=True).sort_values(["timestamp", "asset"]).reset_index(drop=True)
    if table.duplicated(["asset", "timestamp"]).any():
        raise ValueError("overlapping years: duplicate (asset, timestamp) rows")
    table.to_parquet(_out(out), index=False)
    lab = table["label"]
    print(f"wrote {out}: {len(table)} rows, positives {int((lab == 1).sum())}, negatives {int((lab == 0).sum())}, unknown {int(lab.isna().sum())}")
    return table


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("features")
    f.add_argument("--sensors", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--maintenance-log", default=None, help="CMMS export (maintenance_log.json): windows reset at repairs")
    c = sub.add_parser("combine")
    c.add_argument("tables", nargs="+")
    c.add_argument("--out", required=True)
    a = p.parse_args(argv)
    if a.cmd == "features":
        features(a.sensors, a.out, a.maintenance_log)
    else:
        combine(a.tables, a.out)


if __name__ == "__main__":
    main()
