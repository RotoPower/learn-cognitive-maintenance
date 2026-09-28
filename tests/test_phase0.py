"""scripts/phase0_staging.py: the pure helpers (the steps themselves talk to staging)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("phase0", Path(__file__).resolve().parents[1] / "scripts" / "phase0_staging.py")
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


def test_sibling_urls_come_from_the_staging_api_url():
    api = "https://plant-api-staging.example.workers.dev"
    assert P.sibling_url(api, "plant-scoring") == "https://plant-scoring-staging.example.workers.dev"
    assert P.sibling_url(api + "/", "plant-dashboard") == "https://plant-dashboard-staging.example.workers.dev"
    for bad in ("http://127.0.0.1:8000", "https://plant-api.example.workers.dev"):  # local, production
        with pytest.raises(SystemExit):
            P.sibling_url(bad, "plant-scoring")


def test_pending_migrations_parsed_from_wrangler_table():
    out = """Migrations to be applied:
+---------------------------+
| Name                      |
+---------------------------+
| 0002_scoring_indexes.sql  |
| 0003_dashboard.sql        |
| 0004_assistant.sql        |
+---------------------------+"""
    assert P.pending_migrations(out) == sorted(P.ALLOWED_MIGRATIONS)
    assert P.pending_migrations("No migrations to apply!") == []
    assert "0005_readings_pk_only.sql" not in P.ALLOWED_MIGRATIONS


def test_dry_run_checks_locally_and_calls_nothing(monkeypatch, capsys):
    monkeypatch.setattr(P, "_load_dotenv", lambda: None)
    monkeypatch.setenv("PLANT_API_URL", "https://plant-api-staging.example.workers.dev")
    monkeypatch.setenv("PLANT_ADMIN_TOKEN", "t-SECRET")
    monkeypatch.setattr(P, "run", lambda *a, **k: pytest.fail("no command in a dry run"))
    monkeypatch.setattr(P, "http", lambda *a, **k: pytest.fail("no request in a dry run"))
    code = P.main(["--dry-run"])
    out = capsys.readouterr().out
    assert "SECRET" not in out
    if code == 0:  # node_modules present
        assert "[6/6] read /api/overview" in out
    else:
        assert code == 3 and "npm install" in out


def test_step1_refuses_a_bulk_delete_without_the_flag(monkeypatch):
    calls = []

    def fake_sql(sql):
        calls.append(sql)
        return [{"n": 1_391_328}] if sql.startswith("SELECT") else []

    monkeypatch.setattr(P, "wrangler_sql", fake_sql)
    with pytest.raises(SystemExit, match="refusing: deleting 1391328 rows"):
        P.step1_delete_junk({})
    assert not any(s.startswith("DELETE") for s in calls)
    calls.clear()
    P.step1_delete_junk({"allow_bulk_delete": True})
    assert any(s.startswith("DELETE") for s in calls)
