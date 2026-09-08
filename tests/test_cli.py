"""Tests for plantctl (plant/cli.py), run in-process against the FastAPI app."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from plant import cli
from plant.api import create_app

ENV_READ = {"PLANT_READ_TOKEN": "r", "PLANT_API_URL": "http://plant"}
ENV_ADMIN = {**ENV_READ, "PLANT_ADMIN_TOKEN": "a"}


@pytest.fixture()
def transport(tmp_path):
    app = create_app(seed=42, clock_start=datetime(2024, 9, 1), speed=0, read_token="r", admin_token="a", artifact_dir=tmp_path / "art")
    client = TestClient(app)

    def send(method, url, body, token):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = client.request(method, url.replace("http://plant", ""), json=body, headers=headers)
        return r.status_code, (r.json() if r.content else None)

    return send


def run(argv, transport, env=ENV_READ):
    code, text = cli.run(argv, transport=transport, env=env)
    return code, text


def test_clock_and_assets(transport) -> None:
    code, text = run(["clock"], transport)
    assert code == 0 and json.loads(text) == {"sim_time": "2024-09-01T00:00:00", "speed": 0.0}
    code, text = run(["assets", "--table"], transport)
    assert code == 0 and text.splitlines()[0].startswith("asset_id") and "BFP2" in text


def test_history_json_csv_table(transport) -> None:
    args = ["history", "--tag", "BFP2.VIB_DE", "--from", "2024-08-30T00:00", "--to", "2024-08-30T03:00"]
    code, text = run(args, transport)
    pts = json.loads(text)["points"]
    assert code == 0 and len(pts) == 4 and pts[0]["timestamp"] == "2024-08-30T00:00:00"
    code, text = run(args + ["--csv"], transport)
    lines = text.strip().splitlines()
    assert lines[0] == "timestamp,BFP2.VIB_DE" and len(lines) == 5
    code, text = run(args + ["--table"], transport)
    assert "4 points" in text.splitlines()[0]
    # hyphenated asset id in the tag is accepted by the API
    code, _ = run(["history", "--tag", "BFP-2.VIB_DE", "--from", "2024-08-30", "--to", "2024-08-30T01:00"], transport)
    assert code == 0


def test_latest_and_maintenance(transport) -> None:
    code, text = run(["latest", "--asset", "BFP-2"], transport)
    vals = json.loads(text)["values"]
    assert code == 0 and "BFP2.FLOW" in vals and "PLANT.LOAD" in vals
    code, text = run(["workorder", "--asset", "BFP2", "--type", "inspection", "--description", "vib check"], transport)
    assert code == 0 and json.loads(text)["id"] == "WO-00001"
    code, text = run(["maintenance-log", "--asset", "BFP2", "--table"], transport)
    assert code == 0 and "vib check" in text


def test_admin_commands_require_flag_and_token(transport) -> None:
    code, text = run(["ground-truth"], transport, ENV_ADMIN)
    assert code == 3 and "--admin" in text
    code, text = run(["--admin", "ground-truth"], transport, ENV_READ)
    assert code == 3 and "PLANT_ADMIN_TOKEN" in text
    code, text = run(["--admin", "ground-truth"], transport, ENV_ADMIN)
    gt = json.loads(text)
    assert code == 0 and [f["asset"] for f in gt["failures"]] == ["BFP2", "GT1", "CTF1"]


def test_admin_with_read_token_is_forbidden(transport) -> None:
    code, text = run(["--admin", "ground-truth"], transport, {**ENV_READ, "PLANT_ADMIN_TOKEN": "r"})
    assert code == 1 and "403" in text


def test_admin_clock_and_faults(transport) -> None:
    code, text = run(["--admin", "jump", "--to", "2024-10-01T00:00"], transport, ENV_ADMIN)
    assert code == 0 and json.loads(text)["sim_time"] == "2024-10-01T00:00:00"
    code, text = run(["--admin", "speed", "--speed", "3600"], transport, ENV_ADMIN)
    assert code == 0 and json.loads(text)["speed"] == 3600
    code, text = run(["--admin", "inject-fault", "--asset", "BFP1", "--mode", "bearing_wear", "--onset", "2024-11-01", "--duration-days", "10"], transport, ENV_ADMIN)
    assert code == 0 and json.loads(text)["failure"] == "2024-11-11T00:00:00"
    code, text = run(["--admin", "reset", "--seed", "7"], transport, ENV_ADMIN)
    assert code == 0 and json.loads(text)["seed"] == 7


def test_upload_artifact_roundtrip(transport, tmp_path) -> None:
    art = {"run_id": "r1", "seed": 42, "task": "predict", "feature_names": ["a"], "scaler": {"mean": [0], "std": [1]},
           "coefficients": [0.5], "intercept": 0.1, "threshold": 0.5, "config": {}, "metrics": {"test": {"pr_auc": 0.9}}}
    f = tmp_path / "r1.json"
    f.write_text(json.dumps(art))
    code, text = run(["--admin", "upload-artifact", "--file", str(f)], transport, ENV_ADMIN)
    assert code == 0 and json.loads(text)["run_id"] == "r1"
    code, text = run(["--admin", "artifacts"], transport, ENV_ADMIN)
    assert json.loads(text) == ["r1"]
    assert json.loads((tmp_path / "art" / "r1.json").read_text())["model"]["coefficients"] == [0.5]


def test_api_errors_and_out_guard(transport, tmp_path) -> None:
    code, text = run(["history", "--tag", "GT1.NOPE", "--from", "2024-08-01", "--to", "2024-08-02"], transport)
    assert code == 1 and "404" in text
    code, text = run(["clock", "--out", str(tmp_path / "data" / "raw" / "c.json")], transport)
    assert code == 3 and "read-only" in text
    code, text = run(["clock", "--out", str(tmp_path / "out" / "c.json")], transport)
    assert code == 0 and (tmp_path / "out" / "c.json").exists()


def test_connection_error_message() -> None:
    def dead(method, url, body, token):
        raise ConnectionError("cannot reach plant API at http://x: refused. Start it with `uv run uvicorn plant.api:app`")

    code, text = cli.run(["clock"], transport=dead, env=ENV_READ)
    assert code == 2 and "uvicorn" in text
