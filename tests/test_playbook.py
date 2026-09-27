"""docs/playbook + scripts/load_playbook.py + the playbook routes of plant/api.py."""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from plant.api import create_app
from plant.sim import FAULT_MODES

_spec = importlib.util.spec_from_file_location("load_playbook", Path(__file__).resolve().parents[1] / "scripts" / "load_playbook.py")
LP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(LP)


@pytest.fixture()
def api(tmp_path):
    app = create_app(seed=42, clock_start=datetime(2024, 9, 1), speed=0, read_token="r", admin_token="a", artifact_dir=tmp_path / "art")
    client = TestClient(app)

    def send(method, url, body, token):
        r = client.request(method, url.replace("http://plant", ""), json=body, headers={"Authorization": f"Bearer {token}"} if token else {})
        return r.status_code, (r.json() if r.content else None)

    return client, send


def test_every_mode_has_every_section() -> None:
    books = LP.load_all()
    assert set(books) == set(FAULT_MODES) and len(books) == 8
    for mode, s in books.items():
        assert set(s) == {"symptoms", "checks", "actions", "spares", "lead_time"}, mode
        actions = [line for line in s["actions"].splitlines() if line.strip()]
        assert actions and all(line.startswith("- ") for line in actions), mode  # the dashboard lists these bullets


def test_playbooks_cite_the_right_tags() -> None:
    books = LP.load_all()
    assert "VIB_DE" in books["bearing_wear"]["symptoms"] and "BRG_TEMP_DE" in books["bearing_wear"]["symptoms"]
    assert "CDP" in books["compressor_fouling"]["symptoms"] and "EXH_TEMP" in books["compressor_fouling"]["symptoms"]
    assert "GBX_OIL_TEMP" in books["gearbox_wear"]["symptoms"]


def test_parse_rejects_unknown_or_missing_sections() -> None:
    with pytest.raises(ValueError, match="unknown playbook section"):
        LP.parse("## Symptoms\n- a\n## Vibes\n- b\n")
    with pytest.raises(ValueError, match="missing or empty sections"):
        LP.parse("## Symptoms\n- a\n")


def test_load_roundtrip_through_the_admin_api(api) -> None:
    client, send = api
    code = LP.main([], transport=send, env={"PLANT_API_URL": "http://plant", "PLANT_ADMIN_TOKEN": "a"})
    assert code == 0
    got = client.get("/playbook", params={"mode": "gearbox_wear"}, headers={"Authorization": "Bearer r"}).json()
    assert set(got) == {"gearbox_wear"} and got["gearbox_wear"]["actions"] == LP.load_all()["gearbox_wear"]["actions"]
    assert len(client.get("/playbook", headers={"Authorization": "Bearer r"}).json()) == len(FAULT_MODES) == 8


def test_playbook_routes_guard_input(api) -> None:
    client, _ = api
    admin, read = {"Authorization": "Bearer a"}, {"Authorization": "Bearer r"}
    assert client.post("/admin/playbook", json={"mode": "bearing_wear", "sections": {"actions": "- x"}}, headers=read).status_code == 403
    assert client.post("/admin/playbook", json={"mode": "nope", "sections": {"actions": "- x"}}, headers=admin).status_code == 422
    assert client.post("/admin/playbook", json={"mode": "bearing_wear", "sections": {"vibes": "- x"}}, headers=admin).status_code == 422
    assert client.post("/admin/playbook", json={"mode": "bearing_wear", "sections": {}}, headers=admin).status_code == 422
    assert LP.main([], transport=lambda *a: (401, {"detail": "no"}), env={"PLANT_ADMIN_TOKEN": "bad"}) == 1
    assert LP.main([], env={}) == 3


def test_each_playbook_names_its_modes_symptom_tags() -> None:
    books = LP.load_all()
    for mode, symptoms in FAULT_MODES.items():
        text = books[mode]["symptoms"]
        missing = [t for t in symptoms if t not in text]
        assert not missing, (mode, missing)
