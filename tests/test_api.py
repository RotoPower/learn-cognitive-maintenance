"""Tests for plant/api.py."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from plant.api import SimClock, create_app
from plant.sim import Plant

READ = {"Authorization": "Bearer r"}
ADMIN = {"Authorization": "Bearer a"}


class FakeTime:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture()
def env(tmp_path):
    ft = FakeTime()
    app = create_app(
        seed=42,
        clock_start=datetime(2024, 9, 1),
        speed=60.0,
        read_token="r",
        admin_token="a",
        artifact_dir=tmp_path / "artifacts",
        real_now=ft,
    )
    return TestClient(app), ft, app


# ----- clock ---------------------------------------------------------------- #


def test_simclock_math() -> None:
    ft = FakeTime()
    c = SimClock(datetime(2024, 1, 1), speed=3600, real_now=ft)
    ft.advance(2)  # 2 real seconds = 2 sim hours
    assert c.now() == datetime(2024, 1, 1, 2)
    c.set_speed(0)
    ft.advance(100)
    assert c.now() == datetime(2024, 1, 1, 2)
    c.jump(datetime(2024, 6, 1))
    assert c.now() == datetime(2024, 6, 1)


def test_clock_routes(env) -> None:
    c, ft, _ = env
    r = c.get("/clock", headers=READ)
    assert r.status_code == 200 and r.json() == {"sim_time": "2024-09-01T00:00:00", "speed": 60.0}
    ft.advance(60)  # one real minute at 60x = one sim hour
    assert c.get("/clock", headers=READ).json()["sim_time"] == "2024-09-01T01:00:00"

    assert c.post("/clock/speed", json={"speed": 3600}, headers=READ).status_code == 403
    r = c.post("/clock/speed", json={"speed": 3600}, headers=ADMIN)
    assert r.json()["speed"] == 3600
    ft.advance(1)
    assert c.get("/clock", headers=READ).json()["sim_time"] == "2024-09-01T02:00:00"

    r = c.post("/clock/jump", json={"to": "2024-10-01T00:00"}, headers=ADMIN)
    assert r.json()["sim_time"] == "2024-10-01T00:00:00"
    assert c.post("/clock/jump", json={"to": "2030-01-01T00:00"}, headers=ADMIN).status_code == 422


# ----- auth ----------------------------------------------------------------- #


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/clock", None),
        ("get", "/assets", None),
        ("get", "/tags/latest", None),
        ("get", "/tags/GT1.EXH_TEMP/history?from=2024-08-01T00:00&to=2024-08-02T00:00", None),
        ("get", "/maintenance/log", None),
        ("post", "/maintenance/workorder", {"asset_id": "GT1"}),
    ],
)
def test_read_routes_need_a_token(env, method, path, body) -> None:
    c, _, _ = env
    kw = {"json": body} if body is not None else {}
    assert getattr(c, method)(path, **kw).status_code == 401
    assert getattr(c, method)(path, headers=READ, **kw).status_code in (200, 201)
    assert getattr(c, method)(path, headers=ADMIN, **kw).status_code in (200, 201)


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/admin/ground_truth", None),
        ("post", "/admin/inject_fault", {"asset": "BFP1", "mode": "bearing_wear", "onset": "2024-11-01T00:00", "duration_days": 10}),
        ("post", "/admin/reset", {"seed": 42}),
        ("post", "/admin/model_artifacts", {"run_id": "r1", "seed": 42}),
    ],
)
def test_admin_routes_reject_read_token(env, method, path, body) -> None:
    c, _, _ = env
    kw = {"json": body} if body is not None else {}
    assert getattr(c, method)(path, headers=READ, **kw).status_code == 403
    assert getattr(c, method)(path, **kw).status_code == 403
    assert getattr(c, method)(path, headers=ADMIN, **kw).status_code in (200, 201)


def test_x_api_key_header_works(env) -> None:
    c, _, _ = env
    assert c.get("/assets", headers={"X-API-Key": "r"}).status_code == 200


# ----- plant routes --------------------------------------------------------- #


def test_assets(env) -> None:
    c, _, _ = env
    body = c.get("/assets", headers=READ).json()
    assert [a["asset_id"] for a in body] == ["GT1", "BFP1", "BFP2", "CTF1"]
    assert "GT1.EXH_TEMP" in body[0]["tags"]


def test_history_matches_simulator_and_is_pure(env) -> None:
    c, _, _ = env
    plant = Plant.from_yaml(seed=42)
    r = c.get("/tags/BFP2.VIB_DE/history", params={"from": "2024-08-30T00:00", "to": "2024-08-30T05:00", "interval": "1h"}, headers=READ)
    pts = r.json()["points"]
    assert [p["timestamp"] for p in pts] == [f"2024-08-30T0{h}:00:00" for h in range(6)]
    for p in pts:
        assert p["value"] == pytest.approx(plant.value("BFP2", "VIB_DE", datetime.fromisoformat(p["timestamp"])))
    # a second, differently-shaped query returns identical values for the same instants
    r2 = c.get("/tags/BFP2.VIB_DE/history", params={"from": "2024-08-29T00:00", "to": "2024-08-30T05:00", "interval": "1h"}, headers=READ)
    tail = r2.json()["points"][-6:]
    assert tail == pts


def test_history_never_returns_the_future(env) -> None:
    c, _, _ = env  # clock is at 2024-09-01T00:00
    r = c.get("/tags/GT1.CDP/history", params={"from": "2024-08-31T22:00", "to": "2024-09-02T00:00"}, headers=READ)
    stamps = [p["timestamp"] for p in r.json()["points"]]
    assert stamps[-1] == "2024-09-01T00:00:00" and len(stamps) == 3


def test_history_intervals_and_errors(env) -> None:
    c, _, _ = env
    r = c.get("/tags/GT1.CDP/history", params={"from": "2024-08-01T00:00", "to": "2024-08-01T01:00", "interval": "15m"}, headers=READ)
    assert len(r.json()["points"]) == 5
    r = c.get("/tags/GT1.CDP/history", params={"from": "2024-08-01", "to": "2024-08-03", "interval": "1d"}, headers=READ)
    assert len(r.json()["points"]) == 3
    assert c.get("/tags/GT1.CDP/history", params={"from": "2024-08-01", "to": "2024-08-02", "interval": "bogus"}, headers=READ).status_code == 422
    assert c.get("/tags/GT1.NOPE/history", params={"from": "2024-08-01", "to": "2024-08-02"}, headers=READ).status_code == 404
    assert c.get("/tags/ZZ9.X/history", params={"from": "2024-08-01", "to": "2024-08-02"}, headers=READ).status_code == 404


def test_history_outage_is_null(env) -> None:
    c, _, _ = env
    r = c.get("/tags/BFP1.FLOW/history", params={"from": "2024-07-20T00:00", "to": "2024-07-20T03:00"}, headers=READ)
    assert all(p["value"] is None for p in r.json()["points"])


def test_latest(env) -> None:
    c, ft, _ = env
    ft.advance(90)  # 90 real s at 60x = 1.5 sim hours -> latest scan is the 01:00 hour
    r = c.get("/tags/latest", params={"asset_id": "BFP-2"}, headers=READ)
    body = r.json()
    assert body["timestamp"] == "2024-09-01T01:00:00"
    assert set(body["values"]) == {"PLANT.LOAD"} | {f"BFP2.{t}" for t in ["FLOW", "DISCH_PRESS", "VIB_DE", "VIB_NDE", "BRG_TEMP_DE", "MOTOR_CURR"]}
    plant = Plant.from_yaml(seed=42)
    assert body["values"]["BFP2.FLOW"] == pytest.approx(plant.value("BFP2", "FLOW", datetime(2024, 9, 1, 1)))
    all_tags = c.get("/tags/latest", headers=READ).json()["values"]
    assert len(all_tags) == 1 + 7 + 6 + 6 + 4
    assert c.get("/tags/latest", params={"asset_id": "NOPE"}, headers=READ).status_code == 404


def test_maintenance_log_and_workorders(env) -> None:
    c, _, _ = env
    # at 2024-09-01 no scripted repair has happened yet (first failure is 2024-09-27)
    assert c.get("/maintenance/log", headers=READ).json() == []
    r = c.post("/maintenance/workorder", json={"asset_id": "bfp-2", "type": "inspection", "description": "vib check"}, headers=READ)
    assert r.status_code == 201
    wo = r.json()
    assert wo["id"] == "WO-00001" and wo["asset_id"] == "BFP2" and wo["timestamp"] == "2024-09-01T00:00:00"
    assert c.get("/maintenance/log", params={"asset_id": "BFP2"}, headers=READ).json() == [wo]
    assert c.get("/maintenance/log", params={"asset_id": "GT1"}, headers=READ).json() == []

    # jump past the BFP2 failure: the corrective repair appears, but only that one
    c.post("/clock/jump", json={"to": "2024-10-01T00:00"}, headers=ADMIN)
    log = c.get("/maintenance/log", headers=READ).json()
    kinds = [(e["kind"], e["asset_id"]) for e in log]
    assert kinds == [("workorder", "BFP2"), ("corrective_repair", "BFP2")]
    assert log[1]["timestamp"] == "2024-09-27T00:00:00"


# ----- admin ---------------------------------------------------------------- #


def test_ground_truth(env) -> None:
    c, _, _ = env
    gt = c.get("/admin/ground_truth", headers=ADMIN).json()
    assert gt["seed"] == 42
    assert [f["asset"] for f in gt["failures"]] == ["BFP2", "GT1", "CTF1"]
    assert gt["events"][0]["asset"] == "BFP1"
    assert gt["health_now"]["BFP2"] < 1.0  # 2024-09-01 is inside the BFP2 degradation window
    assert gt["health_now"]["GT1"] == 1.0


def test_inject_fault(env) -> None:
    c, _, _ = env
    body = {"asset": "BFP-1", "mode": "bearing_wear", "onset": "2024-11-01T00:00", "duration_days": 10}
    r = c.post("/admin/inject_fault", json=body, headers=ADMIN)
    assert r.status_code == 201 and r.json()["failure"] == "2024-11-11T00:00:00"
    gt = c.get("/admin/ground_truth", headers=ADMIN).json()
    assert ("BFP1", "bearing_wear") in {(f["asset"], f["mode"]) for f in gt["failures"]}

    # the injected fault shows up in the sensors
    c.post("/clock/jump", json={"to": "2024-11-11T00:00"}, headers=ADMIN)
    late = c.get("/tags/BFP1.BRG_TEMP_DE/history", params={"from": "2024-11-10T00:00", "to": "2024-11-10T23:00"}, headers=READ).json()["points"]
    early = c.get("/tags/BFP1.BRG_TEMP_DE/history", params={"from": "2024-10-20T00:00", "to": "2024-10-20T23:00"}, headers=READ).json()["points"]
    assert sum(p["value"] for p in late) / 24 > sum(p["value"] for p in early) / 24 + 10

    # overlapping injection is refused and leaves state unchanged
    r = c.post("/admin/inject_fault", json={**body, "onset": "2024-11-05T00:00"}, headers=ADMIN)
    assert r.status_code == 409
    assert len(c.get("/admin/ground_truth", headers=ADMIN).json()["failures"]) == 4
    assert c.post("/admin/inject_fault", json={**body, "mode": "nope"}, headers=ADMIN).status_code == 422


def test_reset(env) -> None:
    c, _, app = env
    c.post("/admin/inject_fault", json={"asset": "BFP1", "mode": "bearing_wear", "onset": "2024-11-01T00:00", "duration_days": 5}, headers=ADMIN)
    c.post("/maintenance/workorder", json={"asset_id": "GT1"}, headers=READ)
    before = c.get("/tags/GT1.EXH_TEMP/history", params={"from": "2024-03-01", "to": "2024-03-01"}, headers=READ).json()["points"][0]["value"]

    r = c.post("/admin/reset", json={"seed": 7}, headers=ADMIN)
    assert r.json() == {"seed": 7, "sim_time": "2024-01-01T00:00:00", "speed": 60.0}
    assert len(c.get("/admin/ground_truth", headers=ADMIN).json()["failures"]) == 3
    assert app.state.sim.workorders == []
    c.post("/clock/jump", json={"to": "2024-06-01T00:00"}, headers=ADMIN)
    after = c.get("/tags/GT1.EXH_TEMP/history", params={"from": "2024-03-01", "to": "2024-03-01"}, headers=READ).json()["points"][0]["value"]
    assert after != before
    assert after == pytest.approx(Plant.from_yaml(seed=7).value("GT1", "EXH_TEMP", datetime(2024, 3, 1)))


def test_model_artifacts(env, tmp_path) -> None:
    c, _, _ = env
    body = {"run_id": "colab-2024-09-07-a", "seed": 42, "model": {"type": "isoforest", "params": {"n": 200}}, "metrics": {"auc": 0.91}}
    r = c.post("/admin/model_artifacts", json=body, headers=ADMIN)
    assert r.status_code == 201
    path = tmp_path / "artifacts" / "colab-2024-09-07-a.json"
    assert path.exists()
    import json

    saved = json.loads(path.read_text())
    assert saved["seed"] == 42 and saved["metrics"]["auc"] == 0.91 and "uploaded_at" in saved
    assert c.get("/admin/model_artifacts", headers=ADMIN).json() == ["colab-2024-09-07-a"]
    assert c.post("/admin/model_artifacts", json={"run_id": "../evil", "seed": 1}, headers=ADMIN).status_code == 422
