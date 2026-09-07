"""Tests for plant/sim.py."""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pytest

from plant import sim
from plant.sim import ASSETS, FAULT_MODES, TAGS, Plant

H = 24.0  # hours per day


@pytest.fixture(scope="module")
def plant() -> Plant:
    return Plant.from_yaml()


def _hours(start_day: float, end_day: float, step_h: float = 1.0) -> np.ndarray:
    return np.arange(start_day * H, end_day * H, step_h)


# --------------------------------------------------------------------------- #
# Seed reproducibility
# --------------------------------------------------------------------------- #


def test_same_seed_same_values(plant: Plant) -> None:
    other = Plant.from_yaml()
    for asset in ASSETS:
        for tag in TAGS[asset]:
            for t in (0.0, 1234.5, 8000.0):
                a, b = plant.value(asset, tag, t), other.value(asset, tag, t)
                assert a == b or (math.isnan(a) and math.isnan(b)), (asset, tag, t)


def test_pure_in_time_order(plant: Plant) -> None:
    """Evaluation order / previous calls must not matter."""
    forward = [plant.value("GT1", "EXH_TEMP", t) for t in range(0, 100)]
    backward = [plant.value("GT1", "EXH_TEMP", t) for t in reversed(range(0, 100))]
    assert forward == backward[::-1]


def test_different_seed_different_noise() -> None:
    p42, p43 = Plant.from_yaml(seed=42), Plant.from_yaml(seed=43)
    diffs = [p42.value("BFP1", "FLOW", t) - p43.value("BFP1", "FLOW", t) for t in range(0, 48)]
    assert any(abs(d) > 1e-9 for d in diffs)
    # ...but the script (ground truth) is seed independent
    assert p42.failures() == p43.failures()


def test_module_level_function_matches_plant(plant: Plant) -> None:
    ts = datetime(2024, 3, 5, 14)
    assert sim.value(42, "BFP-2", "VIB_DE", ts) == plant.value("BFP2", "VIB_DE", ts)
    assert sim.failures(42) == plant.failures()


def test_generate_is_reproducible(plant: Plant) -> None:
    a = Plant.from_yaml().generate(freq_h=6.0)
    b = Plant.from_yaml().generate(freq_h=6.0)
    assert a.equals(b)


def test_datetime_and_hours_agree(plant: Plant) -> None:
    ts = datetime(2024, 1, 3, 6)  # 2 days + 6 h
    assert plant.value("CTF1", "VIB", ts) == plant.value("CTF1", "VIB", 54.0)


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #


def test_health_bounds_and_healthy_by_default(plant: Plant) -> None:
    for asset in ASSETS:
        for t in _hours(0, 365, 6.0):
            h = plant.health(asset, t)
            assert 0.0 <= h <= 1.0
    # BFP1 only has an outage, never degrades
    assert all(plant.health("BFP1", t) == 1.0 for t in _hours(0, 365, 3.0))


def test_health_monotonic_between_repairs(plant: Plant) -> None:
    repairs = {(f["asset"], plant.to_hours(f["repair"])) for f in plant.failures()}
    for asset in ASSETS:
        hours = _hours(0, 365, 1.0)
        prev = plant.health(asset, hours[0])
        for t in hours[1:]:
            cur = plant.health(asset, t)
            # a step *up* is allowed only immediately after a repair instant
            repaired = any(a == asset and t - 1.0 <= r < t for a, r in repairs)
            if cur > prev + 1e-12:
                assert repaired, f"{asset} health rose at h={t} without a repair"
            prev = cur


def test_health_reaches_zero_then_repaired(plant: Plant) -> None:
    for f in plant.failures():
        onset, fail = plant.to_hours(f["onset"]), plant.to_hours(f["failure"])
        a = f["asset"]
        assert plant.health(a, onset) == pytest.approx(1.0)
        assert plant.health(a, fail) == pytest.approx(0.0)
        assert plant.health(a, fail - 1.0) < 0.05
        assert plant.health(a, fail + 1.0) == 1.0
        assert plant.health(a, onset - 1.0) == 1.0


def test_failures_match_script(plant: Plant) -> None:
    got = {(f["asset"], f["mode"], f["onset"], f["failure"]) for f in plant.failures()}
    d = lambda day: datetime(2024, 1, 1) + (datetime(2024, 1, 2) - datetime(2024, 1, 1)) * day  # noqa: E731
    assert got == {
        ("BFP2", "bearing_wear", d(240), d(270)),
        ("GT1", "compressor_fouling", d(270), d(330)),
        ("CTF1", "gearbox_wear", d(300), d(325)),
    }
    assert plant.events() == [{"asset": "BFP1", "event": "sensor_outage", "from": d(200), "to": d(203)}]


def test_overlapping_scenarios_rejected() -> None:
    cfg = {
        "seed": 1,
        "start": "2024-01-01",
        "horizon_days": 10,
        "scenarios": [
            {"asset": "GT1", "mode": "compressor_fouling", "onset_day": 1, "duration_days": 5},
            {"asset": "GT1", "mode": "compressor_fouling", "onset_day": 3, "duration_days": 5},
        ],
    }
    with pytest.raises(ValueError):
        Plant.from_config(cfg)


# --------------------------------------------------------------------------- #
# Symptoms respond to health
# --------------------------------------------------------------------------- #


def _mean(plant: Plant, asset: str, tag: str, hours: np.ndarray) -> float:
    """Mean sensor value with the (known) load effect removed, so only
    baseline + symptom + noise remain."""
    spec = TAGS[asset][tag]
    resid = [plant.value(asset, tag, t) - spec.load_gain * (plant.load(t) - sim.REF_LOAD) for t in hours]
    return float(np.nanmean(resid))


@pytest.mark.parametrize("asset,mode", [("BFP2", "bearing_wear"), ("GT1", "compressor_fouling"), ("CTF1", "gearbox_wear")])
def test_symptoms_move_in_scripted_direction(plant: Plant, asset: str, mode: str) -> None:
    f = next(f for f in plant.failures() if f["asset"] == asset)
    onset, fail = plant.to_hours(f["onset"]), plant.to_hours(f["failure"])
    healthy = np.arange(onset - 14 * H, onset, 1.0)  # two weeks before onset
    sick = np.arange(fail - 3 * H, fail, 1.0)  # last three days before failure
    for tag, (gain, _shape) in FAULT_MODES[mode].items():
        spec = TAGS[asset][tag]
        delta = _mean(plant, asset, tag, sick) - _mean(plant, asset, tag, healthy)
        # require a shift of at least 40% of the full-damage gain and well above noise
        assert math.copysign(1, delta) == math.copysign(1, gain), (tag, delta)
        assert abs(delta) > 0.4 * abs(gain) and abs(delta) > 3 * spec.noise / math.sqrt(len(sick)), (tag, delta)


def test_symptom_grows_with_damage(plant: Plant) -> None:
    """Averaged symptom is monotone in (1 - health) across the degradation window."""
    f = next(f for f in plant.failures() if f["asset"] == "BFP2")
    onset, fail = plant.to_hours(f["onset"]), plant.to_hours(f["failure"])
    edges = np.linspace(onset, fail, 6)
    means = [_mean(plant, "BFP2", "BRG_TEMP_DE", np.arange(a, b, 1.0)) for a, b in zip(edges, edges[1:])]
    assert all(b > a for a, b in zip(means, means[1:])), means


def test_no_symptom_when_healthy_or_after_repair(plant: Plant) -> None:
    f = next(f for f in plant.failures() if f["asset"] == "GT1")
    onset, fail = plant.to_hours(f["onset"]), plant.to_hours(f["failure"])
    before = _mean(plant, "GT1", "CDP", np.arange(onset - 7 * H, onset, 1.0))
    after = _mean(plant, "GT1", "CDP", np.arange(fail, fail + 7 * H, 1.0))
    assert after == pytest.approx(before, abs=0.15)


def test_unaffected_asset_unchanged_during_others_fault(plant: Plant) -> None:
    f = next(f for f in plant.failures() if f["asset"] == "BFP2")
    onset, fail = plant.to_hours(f["onset"]), plant.to_hours(f["failure"])
    a = _mean(plant, "BFP1", "VIB_DE", np.arange(onset - 7 * H, onset, 1.0))
    b = _mean(plant, "BFP1", "VIB_DE", np.arange(fail - 7 * H, fail, 1.0))
    assert b == pytest.approx(a, abs=0.1)


def test_load_effect_present(plant: Plant) -> None:
    """Flow follows plant load: high-load hours read higher than low-load hours."""
    hours = _hours(10, 40, 1.0)
    loads = np.array([plant.load(t) for t in hours])
    flow = np.array([plant.value("BFP1", "FLOW", t) for t in hours])
    assert 0.45 <= loads.min() and loads.max() <= 1.0
    assert np.corrcoef(loads, flow)[0, 1] > 0.95
    assert plant.value("PLANT", "LOAD", 100.0) == plant.load(100.0)


# --------------------------------------------------------------------------- #
# Realism quirks
# --------------------------------------------------------------------------- #


def test_sensor_outage_reads_nan(plant: Plant) -> None:
    for tag in TAGS["BFP1"]:
        assert math.isnan(plant.value("BFP1", tag, 201 * H))
        assert not math.isnan(plant.value("BFP1", tag, 199 * H))
        assert not math.isnan(plant.value("BFP1", tag, 203 * H))
    assert not math.isnan(plant.value("BFP2", "FLOW", 201 * H))


def test_generated_frame_has_quirks(plant: Plant) -> None:
    df = plant.generate()
    ts = df["timestamp"]

    # dead tag: constant for the whole horizon
    assert df["GT1.BRG_TEMP_2"].nunique() == 1

    # exactly one duplicated timestamp with identical values
    dups = df[ts.duplicated(keep=False)]
    assert len(dups) == 2 and dups.iloc[0].equals(dups.iloc[1])
    assert dups["timestamp"].iloc[0] == datetime(2024, 1, 1) + np.timedelta64(87 * 24 + 14, "h")

    # a few missing hours: one gap of 6 h between consecutive rows
    gaps = ts.drop_duplicates().diff().dropna()
    assert (gaps == np.timedelta64(6, "h")).sum() == 1
    assert (gaps == np.timedelta64(1, "h")).sum() == len(gaps) - 1

    # outage shows as NaN for BFP1 only
    outage = (ts >= datetime(2024, 7, 19)) & (ts < datetime(2024, 7, 22))
    assert df.loc[outage, "BFP1.FLOW"].isna().all()
    assert df.loc[~outage, "BFP1.FLOW"].notna().all()
    assert df["BFP2.FLOW"].notna().all()

    # row count: 8760 - 5 missing + 1 duplicate
    assert len(df) == 365 * 24 - 5 + 1


def test_generate_without_quirks_is_clean(plant: Plant) -> None:
    df = plant.generate(freq_h=1.0, quirks=False)
    assert len(df) == 365 * 24
    assert not df["timestamp"].duplicated().any()


def test_write_refuses_data_raw(plant: Plant, tmp_path) -> None:
    with pytest.raises(PermissionError):
        plant.write(tmp_path / "data" / "raw")


def test_write_outputs(plant: Plant, tmp_path) -> None:
    small = Plant.from_config(
        {"seed": 7, "start": "2024-01-01", "horizon_days": 3,
         "scenarios": [{"asset": "CTF-1", "mode": "gearbox_wear", "onset_day": 1, "duration_days": 1}]}
    )
    paths = small.write(tmp_path / "sim")
    assert paths["sensors"].exists() and paths["ground_truth"].exists()
    text = paths["ground_truth"].read_text()
    assert "gearbox_wear" in text and "CTF1" in text
