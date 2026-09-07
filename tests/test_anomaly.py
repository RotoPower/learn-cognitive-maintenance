"""Tests for models/anomaly.py (rolling z-score anomaly detector)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from models import anomaly as A

CFG = A.Config()
REAL_PARQUET = Path("data/derived/anomaly_fleet_2024-09-20.parquet")


def _flat_series(n_hours: int = 24 * 40, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n_hours, freq="h")
    return pd.Series(rng.normal(100.0, 1.0, n_hours), index=idx)


def test_step_is_flagged_and_blip_is_not():
    s = _flat_series()
    step_start = s.index[24 * 30]
    s.loc[step_start : step_start + pd.Timedelta(hours=7)] += 5.0  # 8 h at +5 sigma
    blip_start = s.index[24 * 35]
    s.loc[blip_start : blip_start + pd.Timedelta(hours=2)] += 5.0  # 3 h blip
    runs = A.find_runs(A.zscores(s, CFG)["z"], CFG)
    assert len(runs) == 1
    r = runs[0]
    assert r["first_flag_ts"] == step_start
    assert r["hours_flagged"] == 8
    assert r["severity"] > 3 and r["z_peak_signed"] > 0
    assert not any(x["first_flag_ts"] == blip_start for x in runs)


def test_baseline_excludes_current_and_future_points():
    s = _flat_series()
    t = s.index[24 * 30]
    mean0, _ = A.rolling_baseline(s, CFG)
    s2 = s.copy()
    s2.loc[t] += 1000.0
    mean1, std1 = A.rolling_baseline(s2, CFG)
    # baseline at t and at every earlier hour is unaffected by the spike at t
    assert np.allclose(mean0.loc[:t].dropna(), mean1.loc[:t].dropna())
    # z at t is computed against the clean baseline
    z = A.zscores(s2, CFG)["z"]
    assert z.loc[t] > 100
    # the point after t does see the spike in its baseline
    assert mean1.loc[t + pd.Timedelta(hours=1)] > mean0.loc[t + pd.Timedelta(hours=1)]


def test_nan_breaks_runs():
    s = _flat_series()
    t0 = s.index[24 * 30]
    s.loc[t0 : t0 + pd.Timedelta(hours=9)] += 5.0  # 10 h step
    s.loc[t0 + pd.Timedelta(hours=4)] = np.nan  # 4 hot, NaN, 5 hot -> no run >= 6
    runs = A.find_runs(A.zscores(s, CFG)["z"], CFG)
    assert runs == []


def _long_table():
    idx = pd.date_range("2024-01-01", periods=24 * 40, freq="h")
    rng = np.random.default_rng(1)
    rows = []
    for asset, tag in [("BFP1", "BFP1.VIB_DE"), ("GT1", "GT1.BRG_TEMP_2"), ("GT1", "GT1.VIB_1")]:
        v = rng.normal(1.0, 0.1, len(idx))
        if tag == "GT1.BRG_TEMP_2":
            v = np.full(len(idx), 81.4)
        rows.append(pd.DataFrame({"timestamp": idx, "asset": asset, "tag": tag, "value": v,
                                  "dead": tag == "GT1.BRG_TEMP_2"}))
    return pd.concat(rows, ignore_index=True)


def test_dead_tags_and_outage_windows_are_excluded():
    df = _long_table()
    # a huge 24 h excursion inside an outage window must not be flagged
    o_from, o_to = pd.Timestamp("2024-02-01T00:00"), pd.Timestamp("2024-02-01T23:00")
    m = (df.tag == "BFP1.VIB_DE") & (df.timestamp >= o_from) & (df.timestamp <= o_to)
    df.loc[m, "value"] += 50.0
    meta = {"dead_tags": ["GT1.BRG_TEMP_2"],
            "outage_windows": [{"asset": "BFP1", "from": o_from.isoformat(), "to": o_to.isoformat()}]}
    as_of = df.timestamp.max()
    flags, stats = A.score_table(df, meta, as_of, A.Config(window_days=40))
    assert "GT1.BRG_TEMP_2" not in {s["tag"] for s in stats}
    assert flags == []
    vib = next(s for s in stats if s["tag"] == "BFP1.VIB_DE")
    assert vib["n_nan_values"] == 24
    # and the outage values did not contaminate the baseline of later points
    ex = A.apply_exclusions(df, meta)
    s = ex[ex.tag == "BFP1.VIB_DE"].set_index("timestamp")["value"]
    mean, _ = A.rolling_baseline(s, CFG)
    assert mean.loc[o_to + pd.Timedelta(days=5)] < 2.0


def test_interpretation_directions():
    assert "bearing_wear" in A.interpret("BFP2", "BFP2.VIB_DE", 4.0)
    assert "no documented mode" in A.interpret("BFP2", "BFP2.VIB_DE", -4.0)
    assert "compressor_fouling" in A.interpret("GT1", "GT1.CDP", -4.0)
    assert "not a symptom tag" in A.interpret("GT1", "GT1.VIB_1", 4.0)


@pytest.mark.skipif(not REAL_PARQUET.exists(), reason="prepared parquet missing")
def test_cli_end_to_end(tmp_path):
    art = A.run_score("2024-09-20T00:00:00", 7, str(REAL_PARQUET), 42, 1,
                      report_dir=tmp_path / "reports", artifact_dir=tmp_path / "artifacts")
    assert (tmp_path / "reports" / "anomaly_2024-09-20.md").exists()
    art_path = tmp_path / "artifacts" / "anomaly_fleet_2024-09-20_r1.json"
    assert art_path.exists()
    saved = json.loads(art_path.read_text())
    assert saved["run_id"] == "anomaly_fleet_2024-09-20_r1"
    assert saved["seed"] == 42
    assert saved["config"]["baseline_days"] == 30
    assert "GT1.BRG_TEMP_2" not in {s["tag"] for s in saved["per_tag_stats"]}
    assert saved["metrics"]["n_flags"] == len(saved["flags"])
    # deterministic
    art2 = A.run_score("2024-09-20T00:00:00", 7, str(REAL_PARQUET), 42, 1,
                       report_dir=tmp_path / "r2", artifact_dir=tmp_path / "a2")
    assert json.loads((tmp_path / "a2" / "anomaly_fleet_2024-09-20_r1.json").read_text())["flags"] == \
        json.loads(art_path.read_text())["flags"]
    assert art["metrics"] == art2["metrics"]
