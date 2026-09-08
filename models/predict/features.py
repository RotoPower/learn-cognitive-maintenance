"""Lagged feature table for the predict task (predict skill).

Input: the long table prepared by the data agent (``timestamp, asset, tag,
value, dead``) or the wide simulator CSV. Output: one row per (asset, day)
with, per tag on that asset:

    <TAG>__cur       value at the row timestamp
    <TAG>__mean7d    mean over the trailing 7 days
    <TAG>__slope7d   OLS slope over the trailing 7 days, units/day
    <TAG>__slope30d  OLS slope over the trailing 30 days, units/day

plus ``hours_since_repair`` (from the maintenance log's corrective repairs;
hours since the start of data when the asset has no logged repair) and
``LOAD__cur`` / ``LOAD__mean7d`` from ``PLANT.LOAD`` so a model can
load-normalise. Tag names drop the asset prefix (``VIB_DE`` not
``BFP2.VIB_DE``) so the two pumps share columns; tags an asset does not have
are NaN.

Every feature at time t uses only samples with timestamp <= t. Dead tags are
excluded. NaN values (outages, gaps) never contribute to a window.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

SUFFIXES = ("cur", "mean7d", "slope7d", "slope30d")
LOAD_TAG = "PLANT.LOAD"
MIN_HISTORY_DAYS = 30
ID_COLS = ("asset", "timestamp")


# --------------------------------------------------------------------------- io


def load_long(path: str | Path) -> pd.DataFrame:
    """Read a long table (parquet/csv) or a wide sensor CSV and return long format."""
    path = Path(path)
    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    if {"tag", "value"} <= set(df.columns):
        out = df.copy()
    else:
        out = wide_to_long(df)
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    if "dead" not in out.columns:
        const = out.groupby("tag")["value"].nunique(dropna=True)
        out["dead"] = out["tag"].map(const <= 1).fillna(False).astype(bool)
    return out


def wide_to_long(wide: pd.DataFrame) -> pd.DataFrame:
    tags = [c for c in wide.columns if c != "timestamp"]
    long = wide.melt(id_vars="timestamp", value_vars=tags, var_name="tag", value_name="value")
    long["asset"] = long["tag"].str.split(".", n=1).str[0]
    return long[["timestamp", "asset", "tag", "value"]]


def load_repairs(maintenance_log: str | Path | list[dict] | None) -> pd.DataFrame:
    """Corrective repairs from a GET /maintenance/log dump -> [asset, timestamp]."""
    if maintenance_log is None:
        return pd.DataFrame({"asset": pd.Series(dtype=str), "timestamp": pd.Series(dtype="datetime64[ns]")})
    entries = maintenance_log
    if not isinstance(entries, list):
        entries = json.loads(Path(maintenance_log).read_text(encoding="utf-8"))
    rows = [
        {"asset": e["asset_id"], "timestamp": pd.Timestamp(e["timestamp"])}
        for e in entries
        if e.get("kind") == "corrective_repair"
    ]
    return pd.DataFrame(rows, columns=["asset", "timestamp"])


# ------------------------------------------------------------------- features


def _rolling_slope(x: pd.DataFrame, window: str, min_periods: int) -> pd.DataFrame:
    """Per-column OLS slope over a trailing time window, in units per day.

    Time is masked wherever x is NaN so all four moments run over the same
    sample set.
    """
    t = ((x.index - x.index[0]) / pd.Timedelta(hours=1)).astype(float)
    T = pd.DataFrame(np.tile(t.to_numpy()[:, None], (1, x.shape[1])), index=x.index, columns=x.columns)
    T = T.where(x.notna())

    def r(d: pd.DataFrame) -> pd.DataFrame:
        return d.rolling(window, min_periods=min_periods, closed="right").mean()

    ex, et, ext, ett = r(x), r(T), r(x * T), r(T * T)
    var = ett - et**2
    slope_per_hour = (ext - ex * et) / var.where(var > 1e-9)
    return slope_per_hour * 24.0


def _asset_frame(long: pd.DataFrame, asset: str, load: pd.Series | None) -> pd.DataFrame:
    g = long[(long["asset"] == asset) & (~long["dead"])]
    wide = (
        g.drop_duplicates(["timestamp", "tag"])
        .pivot(index="timestamp", columns="tag", values="value")
        .sort_index()
    )
    wide.columns = [c.split(".", 1)[1] if "." in c else c for c in wide.columns]
    if load is not None:
        wide["LOAD"] = load.reindex(wide.index)
    return wide.astype(float)


def _hours_since_repair(asset: str, grid: pd.DatetimeIndex, repairs: pd.DataFrame, data_start: pd.Timestamp) -> np.ndarray:
    rep = np.sort(repairs.loc[repairs["asset"] == asset, "timestamp"].to_numpy(dtype="datetime64[ns]"))
    grid_np = grid.to_numpy(dtype="datetime64[ns]")
    if rep.size == 0:
        return (grid_np - np.datetime64(data_start, "ns")) / np.timedelta64(1, "h")
    idx = np.searchsorted(rep, grid_np, side="right") - 1
    last = np.where(idx >= 0, rep[np.clip(idx, 0, None)], np.datetime64(data_start, "ns"))
    return (grid_np - last) / np.timedelta64(1, "h")


def build_features(
    long: pd.DataFrame,
    as_of: str | pd.Timestamp | None = None,
    repairs: pd.DataFrame | None = None,
    assets: list[str] | None = None,
    step_hours: int = 24,
    min_history_days: int = MIN_HISTORY_DAYS,
) -> pd.DataFrame:
    """One row per (asset, grid timestamp). See module docstring."""
    long = long.copy()
    long["timestamp"] = pd.to_datetime(long["timestamp"])
    if "dead" not in long.columns:
        long["dead"] = False
    if as_of is not None:
        long = long[long["timestamp"] <= pd.Timestamp(as_of)]
    if long.empty:
        raise ValueError("no samples at or before as_of")
    repairs = repairs if repairs is not None else load_repairs(None)

    data_start = long["timestamp"].min().normalize()
    end = long["timestamp"].max() if as_of is None else pd.Timestamp(as_of)
    grid = pd.date_range(data_start + pd.Timedelta(days=min_history_days), end, freq=f"{step_hours}h")

    load = None
    if (long["tag"] == LOAD_TAG).any():
        load = long[long["tag"] == LOAD_TAG].drop_duplicates("timestamp").set_index("timestamp")["value"].sort_index()

    if assets is None:
        assets = sorted(a for a in long["asset"].unique() if a != "PLANT")

    frames = []
    for asset in assets:
        wide = _asset_frame(long, asset, load)
        if wide.empty:
            continue
        feats = {
            "cur": wide,
            "mean7d": wide.rolling("7D", min_periods=24 * 4, closed="right").mean(),
            "slope7d": _rolling_slope(wide, "7D", 24 * 4),
            "slope30d": _rolling_slope(wide, "30D", 24 * 15),
        }
        parts = []
        for suffix, f in feats.items():
            f = f.copy()
            f.columns = [f"{c}__{suffix}" for c in f.columns]
            parts.append(f)
        table = pd.concat(parts, axis=1)
        table = table.reindex(grid, method="pad", tolerance=pd.Timedelta(hours=6))
        table.index.name = "timestamp"
        table = table.reset_index()
        table.insert(0, "asset", asset)
        table["hours_since_repair"] = _hours_since_repair(asset, grid, repairs, data_start)
        frames.append(table)

    out = pd.concat(frames, ignore_index=True)
    # LOAD slopes are not informative features; keep cur and mean only
    out = out.drop(columns=[c for c in out.columns if c.startswith("LOAD__slope")], errors="ignore")
    return out.sort_values(["timestamp", "asset"]).reset_index(drop=True)


def feature_columns(table: pd.DataFrame) -> list[str]:
    """Model input columns: everything except ids and the label."""
    return [c for c in table.columns if c not in (*ID_COLS, "label")]
