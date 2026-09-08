"""Tests for models/predict (features, labels, train, score, upload, CLI)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from models.predict import __main__ as cli
from models.predict import features as F
from models.predict import labels as L
from models.predict import model as M
from plant.api import create_app
from plant.sim import Plant

# A richer script than plant/faults.yaml: every mode fails at least twice before the
# cutoff (2025-01-01) and once in the replay window, so a pooled model can learn.
CFG = {
    "seed": 7,
    "start": "2024-01-01",
    "horizon_days": 540,
    "scenarios": [
        {"asset": "BFP-1", "mode": "bearing_wear", "onset_day": 60, "duration_days": 30},
        {"asset": "BFP-2", "mode": "bearing_wear", "onset_day": 110, "duration_days": 25},
        {"asset": "GT-1", "mode": "compressor_fouling", "onset_day": 40, "duration_days": 60},
        {"asset": "CTF-1", "mode": "gearbox_wear", "onset_day": 150, "duration_days": 25},
        {"asset": "BFP-1", "mode": "bearing_wear", "onset_day": 220, "duration_days": 35},
        {"asset": "GT-1", "mode": "compressor_fouling", "onset_day": 230, "duration_days": 50},
        {"asset": "CTF-1", "mode": "gearbox_wear", "onset_day": 260, "duration_days": 30},
        {"asset": "BFP-2", "mode": "bearing_wear", "onset_day": 400, "duration_days": 30},
        {"asset": "GT-1", "mode": "compressor_fouling", "onset_day": 420, "duration_days": 60},
        {"asset": "CTF-1", "mode": "gearbox_wear", "onset_day": 470, "duration_days": 25},
        {"asset": "BFP-1", "event": "sensor_outage", "from_day": 300, "to_day": 303},
    ],
}
CUTOFF = "2025-01-01"
H = 30


@pytest.fixture(scope="module")
def plant() -> Plant:
    return Plant.from_config(CFG)


@pytest.fixture(scope="module")
def long(plant: Plant) -> pd.DataFrame:
    df = F.wide_to_long(plant.generate(quirks=False))
    df["dead"] = df["tag"].eq("GT1.BRG_TEMP_2")
    return df


@pytest.fixture(scope="module")
def feats(long: pd.DataFrame) -> pd.DataFrame:
    return F.build_features(long)


@pytest.fixture(scope="module")
def failures(plant: Plant) -> pd.DataFrame:
    gt = {"failures": [{"asset": f["asset"], "failure": f["failure"].isoformat()} for f in plant.failures()]}
    return L.load_failures(gt)


@pytest.fixture(scope="module")
def table(feats: pd.DataFrame, failures: pd.DataFrame) -> pd.DataFrame:
    return L.attach_labels(feats, failures, H)


@pytest.fixture(scope="module")
def art(table: pd.DataFrame) -> dict:
    return M.train(table, horizon_days=H, seed=42, cutoff=CUTOFF)


# ------------------------------------------------------------------- features


def test_feature_columns_and_exclusions(feats: pd.DataFrame) -> None:
    cols = F.feature_columns(feats)
    assert {"VIB_DE__cur", "VIB_DE__mean7d", "VIB_DE__slope7d", "VIB_DE__slope30d", "hours_since_repair", "LOAD__cur"} <= set(cols)
    assert not any(c.startswith("BRG_TEMP_2") for c in cols), "dead tag must be excluded"
    assert not any(c.startswith("LOAD__slope") for c in cols)
    assert not any(c.startswith("PLANT") for c in cols)
    assert set(feats["asset"]) == {"GT1", "BFP1", "BFP2", "CTF1"}
    assert set(feats.columns) & {"label", "health", "failure", "rul"} == set()
    # one row per asset per day
    assert feats.groupby("asset").size().nunique() == 1
    # pump-only tags are NaN on the turbine, and vice versa
    gt1 = feats[feats["asset"] == "GT1"]
    assert gt1["VIB_DE__cur"].isna().all() and gt1["EXH_TEMP__cur"].notna().all()


def test_features_use_only_past_data(long: pd.DataFrame) -> None:
    """Truncating the input at T must give the same rows as as_of=T on the full input."""
    trunc = F.build_features(long[long["timestamp"] <= "2024-06-01"])
    full = F.build_features(long, as_of="2024-06-01")
    pd.testing.assert_frame_equal(trunc, full)


def test_slope_on_synthetic_ramp() -> None:
    idx = pd.date_range("2024-01-01", periods=24 * 40, freq="h")
    ramp = pd.DataFrame({"X": np.arange(len(idx)) / 24.0}, index=idx)  # +1 unit per day
    s7 = F._rolling_slope(ramp, "7D", 24 * 4)["X"].iloc[-1]
    s30 = F._rolling_slope(ramp, "30D", 24 * 15)["X"].iloc[-1]
    assert s7 == pytest.approx(1.0, abs=1e-6) and s30 == pytest.approx(1.0, abs=1e-6)


def test_hours_since_repair(long: pd.DataFrame) -> None:
    repairs = F.load_repairs([
        {"kind": "corrective_repair", "asset_id": "BFP1", "timestamp": "2024-03-31T00:00:00"},
        {"kind": "workorder", "asset_id": "BFP1", "timestamp": "2024-03-01T00:00:00"},
    ])
    f = F.build_features(long[long["timestamp"] <= "2024-05-01"], repairs=repairs)
    b = f[f["asset"] == "BFP1"].set_index("timestamp")["hours_since_repair"]
    assert b.loc["2024-03-30"] == 89 * 24  # since data start 2024-01-01
    assert b.loc["2024-03-31"] == 0
    assert b.loc["2024-04-10"] == 10 * 24
    other = f[f["asset"] == "GT1"].set_index("timestamp")["hours_since_repair"]
    assert other.loc["2024-04-10"] == 100 * 24


def test_outage_does_not_poison_windows(long: pd.DataFrame, feats: pd.DataFrame) -> None:
    # BFP1 outage days 300-303 (2024-10-27..30): cur is NaN inside, 7d mean survives from the other days
    b = feats[feats["asset"] == "BFP1"].set_index("timestamp")
    assert np.isnan(b.loc["2024-10-28", "FLOW__cur"])
    assert not np.isnan(b.loc["2024-10-28", "FLOW__mean7d"])
    assert not np.isnan(b.loc["2024-11-02", "FLOW__cur"])


def test_load_long_from_wide_csv(plant: Plant, tmp_path: Path) -> None:
    p = tmp_path / "sensors.csv"
    plant.generate(quirks=True).head(24 * 40).to_csv(p, index=False)
    long = F.load_long(p)
    assert {"timestamp", "asset", "tag", "value", "dead"} <= set(long.columns)
    assert long.loc[long["tag"] == "GT1.BRG_TEMP_2", "dead"].all()
    assert not long.loc[long["tag"] == "GT1.EXH_TEMP", "dead"].any()


# --------------------------------------------------------------------- labels


def test_labels_window_and_unknown() -> None:
    feats = pd.DataFrame({
        "asset": ["A"] * 6 + ["B"] * 6,
        "timestamp": list(pd.date_range("2024-01-01", periods=6, freq="D")) * 2,
        "x": 0.0,
    })
    fails = pd.DataFrame({"asset": ["A"], "failure": [pd.Timestamp("2024-01-04T12:00")]})
    lab = L.make_labels(feats, fails, horizon_days=2, known_until="2024-01-07")
    a = lab[:6].tolist()
    # positive when t < fail <= t + 2d: t = Jan 3, Jan 4 (Jan 2 + 2d = Jan 4 00:00 < fail)
    assert a[:2] == [0.0, 0.0] and a[2:4] == [1.0, 1.0] and a[4] == 0.0
    assert np.isnan(a[5])  # Jan 6 + 2d > known_until
    assert (lab[6:11] == 0).all()  # asset B never fails


def test_labels_output_has_no_ground_truth_columns(table: pd.DataFrame) -> None:
    assert "label" in table.columns
    assert set(table.columns) & {"failure", "failure_est", "mode", "health", "onset"} == set()
    assert table["label"].isna().sum() == 4 * H  # the last H days per asset are unknowable


def test_labels_cli(tmp_path: Path, feats: pd.DataFrame, plant: Plant, capsys) -> None:
    fpath = tmp_path / "f.parquet"
    feats.to_parquet(fpath, index=False)
    gt = tmp_path / "ground_truth.json"
    gt.write_text(json.dumps(plant.ground_truth(), default=str))
    out = tmp_path / "labelled.parquet"
    L.main(["--features", str(fpath), "--horizon-days", "30", "--ground-truth", str(gt), "--out", str(out)])
    t = pd.read_parquet(out)
    assert "label" in t.columns and (t["label"] == 1).sum() > 0
    assert "positives" in capsys.readouterr().out
    with pytest.raises(PermissionError):
        L.main(["--features", str(fpath), "--ground-truth", str(gt), "--out", str(tmp_path / "data" / "raw" / "x.parquet")])


# ---------------------------------------------------------------------- train


def test_split_is_time_based_with_embargo(table: pd.DataFrame) -> None:
    tr, te = M.split(table.assign(timestamp=pd.to_datetime(table["timestamp"])), pd.Timestamp(CUTOFF), H)
    assert tr["timestamp"].max() <= pd.Timestamp(CUTOFF) - pd.Timedelta(days=H)
    assert te["timestamp"].min() > pd.Timestamp(CUTOFF)
    assert tr["label"].notna().all() and te["label"].notna().all()


def test_artifact_shape(art: dict) -> None:
    for k in ("run_id", "seed", "horizon_days", "cutoff", "feature_names", "scaler", "coefficients", "intercept", "threshold", "metrics", "split"):
        assert k in art
    assert len(art["coefficients"]) == len(art["feature_names"]) == len(art["scaler"]["mean"]) == len(art["scaler"]["std"])
    assert art["run_id"] == "predict_fleet_h30_2025-01-01_s42"
    assert art["split"]["train_end"] <= "2024-12-02T00:00:00"
    assert art["split"]["test_start"] > CUTOFF
    json.dumps(art)  # serialisable


def test_backtest_metrics(art: dict) -> None:
    te = art["metrics"]["test"]
    assert te["pr_auc"] > 0.5 and te["pr_auc"] > 2 * te["positive_rate"]
    assert te["precision_at_5"] >= 0.6
    assert te["failures_in_window"] == 3 and te["failures_alerted"] == 3
    assert all(lt["lead_time_days"] >= 7 for lt in te["lead_times"])
    assert te["false_alerts_per_asset_month"] < 2
    assert {lt["asset"] for lt in te["lead_times"]} == {"BFP2", "GT1", "CTF1"}


def test_training_is_deterministic(table: pd.DataFrame, art: dict) -> None:
    again = M.train(table, horizon_days=H, seed=42, cutoff=CUTOFF)
    assert again["coefficients"] == art["coefficients"] and again["threshold"] == art["threshold"]
    other = M.train(table, horizon_days=H, seed=43, cutoff=CUTOFF)
    assert other["run_id"] != art["run_id"]
    assert np.allclose(other["coefficients"], art["coefficients"], atol=1e-4)  # seed only jitters


def test_train_refuses_bad_input(feats: pd.DataFrame, table: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="label"):
        M.train(feats, horizon_days=H)
    with pytest.raises(ValueError, match="positive"):
        M.train(table, horizon_days=H, cutoff="2024-03-01")  # nothing has failed yet


def test_metric_helpers() -> None:
    y = np.array([1, 0, 1, 0, 0])
    assert M.pr_auc(y, np.array([0.9, 0.8, 0.7, 0.2, 0.1])) == pytest.approx((1.0 + 2 / 3) / 2)
    assert M.pr_auc(y, np.array([0.9, 0.1, 0.8, 0.2, 0.3])) == pytest.approx(1.0)
    assert M.precision_at_k(y, np.array([0.9, 0.8, 0.7, 0.2, 0.1]), 2) == 0.5
    assert np.isnan(M.pr_auc(np.zeros(3), np.ones(3)))


# ---------------------------------------------------------------------- score


def test_score_ranks_degrading_asset_first(art: dict, feats: pd.DataFrame, tmp_path: Path) -> None:
    # 2025-03-05: BFP2 fails 2025-03-06 (onset day 400 + 30)
    ranked = M.score(art, feats, "2025-03-05", report_dir=tmp_path)
    assert ranked.iloc[0]["asset"] == "BFP2" and ranked.iloc[0]["alert"]
    assert "bearing_wear" in ranked.iloc[0]["interpretation"]
    assert not ranked.iloc[1:]["alert"].any()
    assert ranked.iloc[1]["interpretation"].startswith("below threshold")
    report = (tmp_path / "predict_2025-03-05.md").read_text(encoding="utf-8")
    assert "| 1 | BFP2 |" in report and "PR-AUC" in report
    # nothing after as_of is used
    assert (ranked["timestamp"] <= pd.Timestamp("2025-03-05")).all()


def test_score_quiet_when_healthy(art: dict, feats: pd.DataFrame) -> None:
    ranked = M.score(art, feats, "2025-01-20", write_report=False)  # no failure until 2025-03-06
    assert not ranked["alert"].any()


def test_artifact_roundtrip_and_predict_proba(art: dict, feats: pd.DataFrame, tmp_path: Path) -> None:
    path = M.save_artifact(art, tmp_path)
    loaded = M.load_artifact(art["run_id"], tmp_path)
    assert loaded["coefficients"] == art["coefficients"]
    sample = feats.tail(20)
    assert np.allclose(M.predict_proba(loaded, sample), M.predict_proba(art, sample))
    # missing columns are tolerated (treated as neutral), extra columns ignored
    p_missing = M.predict_proba(art, sample.drop(columns=["VIB_DE__cur"]).assign(extra=1.0))
    assert p_missing.shape == (20,)


# --------------------------------------------------------------------- upload


def test_upload_posts_to_admin_route(art: dict, tmp_path: Path) -> None:
    app = create_app(seed=42, read_token="r", admin_token="a", artifact_dir=tmp_path / "srv")
    client = TestClient(app)

    def post(url, body, token):
        r = client.post(url.replace("http://plant", ""), json=body, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 201, r.text
        return r.json()

    resp = M.upload(art, "http://plant", "a", _post=post)
    assert resp["run_id"] == art["run_id"]
    saved = json.loads((tmp_path / "srv" / f"{art['run_id']}.json").read_text())
    assert saved["model"]["coefficients"] == art["coefficients"]
    assert saved["metrics"]["test"]["pr_auc"] == art["metrics"]["test"]["pr_auc"]
    # wrong token is refused by the server
    with pytest.raises(AssertionError):
        M.upload(art, "http://plant", "r", _post=post)


# ------------------------------------------------------------------------ CLI


def test_cli_end_to_end(long: pd.DataFrame, plant: Plant, tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    inp = tmp_path / "long.parquet"
    long.to_parquet(inp, index=False)
    gt = tmp_path / "ground_truth.json"
    gt.write_text(json.dumps(plant.ground_truth(), default=str))

    cli.main(["features", "--input", str(inp), "--as-of", "2025-06-01"])
    fpath = Path("data/derived/predict_features_2025-06-01.parquet")
    assert fpath.exists()

    L.main(["--features", str(fpath), "--horizon-days", "30", "--ground-truth", str(gt), "--out", "data/derived/predict_fleet_2025-06-01.parquet"])
    cli.main(["train", "--table", "data/derived/predict_fleet_2025-06-01.parquet", "--horizon-days", "30", "--cutoff", CUTOFF, "--artifact-dir", "art"])
    out = capsys.readouterr().out
    assert "run_id=predict_fleet_h30_2025-01-01_s42" in out and '"pr_auc"' in out

    cli.main(["score", "--artifact", "predict_fleet_h30_2025-01-01_s42", "--as-of", "2025-03-05", "--features", str(fpath), "--artifact-dir", "art", "--report-dir", "rep"])
    out = capsys.readouterr().out
    assert "BFP2" in out.splitlines()[2] and Path("rep/predict_2025-03-05.md").exists()

    with pytest.raises(SystemExit):
        cli.main(["upload", "--artifact", "nope", "--url", "http://x"])  # no token set
