"""Prepare a long-format sensor table for a fleet-wide anomaly run.

Reads the historian export (data/sim/sensors.csv, read-only) and writes:
  - data/derived/anomaly_fleet_<as-of date>.parquet  (long format)
  - data/derived/anomaly_fleet_<as-of date>.meta.json (sidecar)

Never reads data/sim/ground_truth.json and never touches /admin endpoints.

Usage:
    uv run python scripts/prepare_anomaly_table.py \
        --as-of 2024-09-20T00:00:00 --lookback-days 81
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

RAW_CSV = Path("data/sim/sensors.csv")
DERIVED_DIR = Path("data/derived")
REPORTS_DIR = Path("reports")

ASSETS = ["GT1", "BFP1", "BFP2", "CTF1"]
DEAD_TAGS = ["GT1.BRG_TEMP_2"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--as-of", required=True, help="ISO timestamp, e.g. 2024-09-20T00:00:00")
    p.add_argument("--lookback-days", type=int, required=True,
                   help="Days to include before as-of (start = as-of - lookback_days)")
    p.add_argument("--raw-csv", default=str(RAW_CSV))
    p.add_argument("--out-name", default=None,
                   help="Base name (no extension) for outputs; default anomaly_fleet_<as-of date>")
    return p.parse_args()


def find_missing_hour_gaps(idx: pd.DatetimeIndex) -> list[dict]:
    """Given the *actual* sorted unique timestamp index, find gaps > 1h."""
    gaps = []
    idx = idx.sort_values()
    diffs = idx.to_series().diff()
    expected = pd.Timedelta(hours=1)
    for ts, d in diffs.items():
        if pd.notna(d) and d > expected:
            gap_start = ts - d + expected
            gap_end = ts - expected
            n_missing = int(d / expected) - 1
            gaps.append({
                "from": gap_start.isoformat(),
                "to": gap_end.isoformat(),
                "n_missing_hours": n_missing,
            })
    return gaps


def find_outage_windows(df: pd.DataFrame, asset: str, tags: list[str]) -> list[dict]:
    """An outage window is a contiguous run of timestamps where ALL of the asset's
    tags are null."""
    sub = df[tags]
    all_null = sub.isna().all(axis=1)
    windows = []
    in_window = False
    start = None
    prev_ts = None
    for ts, is_null in all_null.items():
        if is_null and not in_window:
            in_window = True
            start = ts
        elif not is_null and in_window:
            in_window = False
            windows.append({"asset": asset, "from": start.isoformat(), "to": prev_ts.isoformat()})
        prev_ts = ts
    if in_window:
        windows.append({"asset": asset, "from": start.isoformat(), "to": prev_ts.isoformat()})
    return windows


def main() -> None:
    args = parse_args()
    as_of = pd.Timestamp(args.as_of)
    start = as_of - pd.Timedelta(days=args.lookback_days)
    raw_path = Path(args.raw_csv)

    df = pd.read_csv(raw_path, parse_dates=["timestamp"])

    n_rows_raw = len(df)
    dup_mask = df.duplicated(subset="timestamp", keep=False)
    duplicated_timestamps = sorted(df.loc[dup_mask, "timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S").unique().tolist())

    # Deduplicate: keep first occurrence (quirk states duplicate rows are identical).
    df = df.drop_duplicates(subset="timestamp", keep="first")
    df = df.set_index("timestamp").sort_index()

    # Missing-hour gaps found in the FULL raw series (documented in sidecar,
    # not filled).
    full_gaps = find_missing_hour_gaps(df.index)

    # Restrict to the requested window: [start, as_of], nothing after as_of.
    windowed = df.loc[(df.index >= start) & (df.index <= as_of)].copy()

    # Outage windows per asset, computed on the windowed slice (only windows
    # that intersect our extraction range are relevant to this table).
    outage_windows: list[dict] = []
    for asset in ASSETS:
        tags = [c for c in windowed.columns if c.startswith(f"{asset}.")]
        outage_windows.extend(find_outage_windows(windowed, asset, tags))

    # Gaps within our extracted window specifically.
    window_gaps = find_missing_hour_gaps(windowed.index)

    # Melt to long format: timestamp, asset, tag, value, dead
    value_cols = [c for c in windowed.columns]
    long_df = windowed.reset_index().melt(id_vars="timestamp", value_vars=value_cols,
                                           var_name="tag", value_name="value")

    def tag_to_asset(tag: str) -> str:
        if tag == "PLANT.LOAD":
            return "PLANT"
        return tag.split(".", 1)[0]

    long_df["asset"] = long_df["tag"].map(tag_to_asset)
    long_df["dead"] = long_df["tag"].isin(DEAD_TAGS)
    long_df = long_df[["timestamp", "asset", "tag", "value", "dead"]]
    long_df = long_df.sort_values(["asset", "tag", "timestamp"]).reset_index(drop=True)

    out_name = args.out_name or f"anomaly_fleet_{as_of.date().isoformat()}"
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    parquet_path = DERIVED_DIR / f"{out_name}.parquet"
    meta_path = DERIVED_DIR / f"{out_name}.meta.json"

    long_df.to_parquet(parquet_path, index=False)

    meta = {
        "source_file": str(raw_path).replace("\\", "/"),
        "as_of": as_of.isoformat(),
        "lookback_days": args.lookback_days,
        "window_start": start.isoformat(),
        "window_end": as_of.isoformat(),
        "row_count_raw_wide": n_rows_raw,
        "row_count_windowed_wide": len(windowed),
        "row_count_long": len(long_df),
        "assets": ASSETS + ["PLANT"],
        "n_tags": len(value_cols),
        "dead_tags": DEAD_TAGS,
        "outage_windows": outage_windows,
        "missing_hour_gaps_in_window": window_gaps,
        "missing_hour_gaps_full_series": full_gaps,
        "duplicated_timestamps_found_raw": duplicated_timestamps,
        "duplicate_handling": "kept first occurrence, dropped exact duplicate row(s)",
        "missing_hours_handling": "left absent, not filled/imputed",
        "notes": [
            "PLANT.LOAD kept as its own asset 'PLANT' with a single tag 'PLANT.LOAD' "
            "(long format, not merged into other assets) so downstream code can join it "
            "onto any asset's rows on timestamp when load-normalising.",
            "dead tag GT1.BRG_TEMP_2 is NOT dropped; rows are flagged via the 'dead' "
            "boolean column so modelers can exclude it from features explicitly.",
            "No ground-truth data (data/sim/ground_truth.json) was read to build this table.",
        ],
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"Wrote {parquet_path} ({len(long_df)} rows)")
    print(f"Wrote {meta_path}")


if __name__ == "__main__":
    main()
