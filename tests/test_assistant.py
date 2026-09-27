"""apps/assistant: the four tools, the draft -> confirm flow, the FastAPI front, and the SDK lockdown.
No call to Claude here: the plant API is faked and chat sessions are stubbed."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from assistant import agent as G
from assistant import app as A
from assistant import tools as T


class FakePlant:
    def __init__(self, playbook: dict | None = None):
        self.playbook = playbook if playbook is not None else {}
        self.posts: list[tuple[str, dict]] = []

    def call(self, method, path, params=None, body=None):
        params = params or {}
        if method == "POST":
            self.posts.append((path, body))
            return {"id": "WO-00007", **body, "status": "open"}
        if path == "/clock":
            return {"sim_time": "2024-09-20T13:00:00", "speed": 60}
        if path == "/tags/latest":
            if params.get("asset_id") == "BFP2":
                return {"timestamp": "2024-09-20T13:00:00", "values": {"BFP2.VIB_DE": 3.1, "BFP2.BRG_TEMP_DE": None}}
            if params.get("asset_id") == "GT1":
                return {"timestamp": "2024-09-20T13:00:00", "values": {"GT1.BRG_TEMP_2": 81.4}}
            return {"timestamp": "2024-09-20T13:00:00", "values": {"PLANT.LOAD": 0.82}}
        if path == "/alerts":
            return [{"asset_id": "BFP2", "tag": "BFP2.VIB_DE", "kind": "anomaly", "first_flag_ts": "2024-09-16T12:00:00",
                     "last_flag_ts": "2024-09-20T13:00:00", "severity": 5.0889, "interpretation": "DE vibration rising", "status": "open"}]
        if path == "/predictions":
            return [{"asset_id": "BFP2", "as_of": "2024-09-20T00:00:00", "p_fail": 0.99131, "threshold": 0.26, "alert": True,
                     "drivers": ["BRG_TEMP_DE__slope7d"], "interpretation": "consistent with bearing_wear", "horizon_days": 30},
                    {"asset_id": "BFP2", "as_of": "2024-09-19T00:00:00", "p_fail": 0.8, "alert": True}][: params.get("limit", 70)]
        if path == "/maintenance/log":
            return [{"kind": "corrective_repair", "asset_id": "BFP2", "timestamp": "2024-03-01T00:00:00"},
                    {"kind": "workorder", "id": "WO-00001", "asset_id": "BFP2", "type": "inspection", "timestamp": "2024-09-18T08:00:00", "status": "open"}]
        if path == "/assets":
            return [{"asset_id": "BFP2", "tags": ["BFP2.VIB_DE"]}]
        if path.startswith("/tags/") and path.endswith("/history"):
            return {"points": [{"timestamp": "2024-09-13T13:00:00", "value": 1.9}, {"timestamp": "2024-09-17T13:00:00", "value": 3.3},
                               {"timestamp": "2024-09-20T13:00:00", "value": 3.1}]}
        if path == "/playbook":
            return self.playbook
        raise AssertionError(f"unexpected {method} {path}")


# ------------------------------------------------------------------ tools


def test_asset_status_cites_units_baselines_and_risk() -> None:
    s = T.get_asset_status(FakePlant(), "BFP-2")
    assert s["asset_id"] == "BFP2" and s["failure_mode_watched"] == "bearing_wear"
    assert s["readings"]["BFP2.VIB_DE"] == {"value": 3.1, "unit": "mm/s", "baseline": 1.8, "vs_baseline_pct": 72.2}
    assert s["readings"]["BFP2.BRG_TEMP_DE"]["note"].startswith("no reading")
    assert s["plant_load"]["value"] == 0.82
    assert s["open_alerts"][0] == {"tag": "BFP2.VIB_DE", "kind": "anomaly", "from": "2024-09-16T12:00:00", "to": "2024-09-20T13:00:00",
                                   "peak": 5.09, "peak_is": "z-score", "reading": "DE vibration rising", "status": "open"}
    assert s["risk"]["p_fail"] == 0.991 and s["risk"]["previous"] == {"as_of": "2024-09-19T00:00:00", "p_fail": 0.8}
    assert s["last_repair"] == "2024-03-01T00:00:00" and s["open_workorders"][0]["id"] == "WO-00001"
    assert T.get_asset_status(FakePlant(), "GT1")["readings"]["GT1.BRG_TEMP_2"]["note"].startswith("dead sensor")
    with pytest.raises(T.ToolError, match="unknown asset"):
        T.get_asset_status(FakePlant(), "PUMP9")


def test_events_window_trend_and_validation() -> None:
    e = T.get_events(FakePlant(), "BFP2")
    assert e["window"] == {"from": "2024-09-13T13:00:00", "to": "2024-09-20T13:00:00"}  # default: last 7 days
    assert e["trend_6h"]["BFP2.VIB_DE"] == {"unit": "mm/s", "baseline": 1.8, "start": 1.9, "end": 3.1, "max": 3.3,
                                           "max_at": "2024-09-17T13:00:00", "samples": 3}
    assert e["risk"]["max_p_fail"] == 0.991 and e["risk"]["alert_days"] == ["2024-09-19T00:00:00", "2024-09-20T00:00:00"]
    assert [m["id"] for m in e["maintenance"]] == ["WO-00001"]  # the March repair is outside the window
    assert T.get_events(FakePlant(), "BFP2", to="2025-06-01")["window"]["to"] == "2024-09-20T13:00:00"  # never past now
    for bad in ({"from_": "2024-09-21"}, {"from_": "2024-01-01"}, {"from_": "yesterday"}):
        with pytest.raises(T.ToolError):
            T.get_events(FakePlant(), "BFP2", **bad)


def test_recommendations_from_d1_local_fallback_query_and_severity() -> None:
    d1 = {"bearing_wear": {"symptoms": "- VIB_DE up", "checks": "- spectrum", "actions": "- re-grease\n- stop above 4.5 mm/s",
                           "spares": "- bearing set", "lead_time": "4 to 5 weeks"}}
    r = T.get_recommendations(FakePlant(d1), "bearing_wear", "critical")
    assert r["source"] == "playbook (D1)" and list(r["sections"])[0] == "actions"
    assert list(T.get_recommendations(FakePlant(d1), "bearing_wear")["sections"])[0] == "symptoms"
    q = T.get_recommendations(FakePlant(d1), "bearing_wear", query="grease")
    assert q["sections"] == {"actions": "- re-grease"}
    local = T.get_recommendations(FakePlant({}), "gearbox_wear")
    assert local["source"] == "docs/playbook/gearbox_wear.md" and "GBX_OIL_TEMP" in local["sections"]["symptoms"]
    with pytest.raises(T.ToolError):
        T.get_recommendations(FakePlant(d1), "rust")


def test_workorder_is_only_a_draft_until_confirmed() -> None:
    api, store = FakePlant(), T.DraftStore()
    d = T.create_workorder(store, "sess-aaaaaaaa", "BFP2", "inspection", "Check DE bearing: VIB_DE 3.1 mm/s at 2024-09-20 13:00")
    assert d["status"] == "awaiting_operator_confirmation" and api.posts == []
    with pytest.raises(T.ToolError):
        T.confirm_workorder(store, api, "sess-otherxxx", d["draft_id"])  # another session cannot confirm it
    wo = T.confirm_workorder(store, api, "sess-aaaaaaaa", d["draft_id"])
    assert wo["id"] == "WO-00007" and api.posts[0][1]["description"].startswith("[assistant] Check DE bearing")
    with pytest.raises(T.ToolError):
        T.confirm_workorder(store, api, "sess-aaaaaaaa", d["draft_id"])  # single use
    with pytest.raises(T.ToolError, match="description"):
        T.create_workorder(store, "sess-aaaaaaaa", "BFP2", "inspection", "  ")
    expired = T.DraftStore(ttl=-1)
    d2 = T.create_workorder(expired, "sess-aaaaaaaa", "GT1", "other", "wash")
    with pytest.raises(T.ToolError):
        T.confirm_workorder(expired, api, "sess-aaaaaaaa", d2["draft_id"])


# ------------------------------------------------------------------ SDK lockdown


def test_sdk_options_are_locked_down() -> None:
    o = G.options(FakePlant(), T.DraftStore(), "sess-aaaaaaaa", "claude-opus-5", None, {"CLAUDE_CODE_OAUTH_TOKEN": "x"})
    assert o.tools == [] and o.setting_sources == [] and o.allowed_tools == G.ALLOWED
    assert set(o.mcp_servers) == {"plant"} and o.model == "claude-opus-5" and o.max_turns == 12
    allow = asyncio.run(G.only_plant_tools("mcp__plant__get_events", {}, None))
    deny = asyncio.run(G.only_plant_tools("Bash", {"command": "ls"}, None))
    assert allow.behavior == "allow" and deny.behavior == "deny"
    assert "Never guess a number" in G.SYSTEM_PROMPT and "cannot confirm" in G.SYSTEM_PROMPT


# ------------------------------------------------------------------ FastAPI front


class StubSession:
    def __init__(self, sid, store):
        self.session_id, self.store, self.lock = sid, store, asyncio.Lock()
        self.last_used = 0.0

    async def turn(self, message):
        yield {"type": "tool", "name": "create_workorder"}
        d = T.create_workorder(self.store, self.session_id, "BFP2", "inspection", f"re: {message}")
        yield {"type": "text", "text": "Drafted; press Confirm."}
        yield {"type": "draft", "draft_id": d["draft_id"]}
        yield {"type": "done", "is_error": False, "turns": 2}

    async def close(self):
        pass


@pytest.fixture()
def client():
    api, store = FakePlant(), T.DraftStore()
    sessions = G.Sessions(lambda sid: StubSession(sid, store), max_sessions=2)
    app = A.create_app(sessions, api, store, secret="s3cret", messages_per_hour=3, confirms_per_hour=5)
    return TestClient(app), api


def test_chat_needs_the_secret_and_streams_ndjson(client) -> None:
    c, api = client
    body = {"message": "BFP2 needs an inspection", "session_id": "sess-aaaaaaaa"}
    assert c.post("/chat", json=body).status_code == 401
    assert c.post("/chat", json=body, headers={"X-Demo-Secret": "nope"}).status_code == 401
    r = c.post("/chat", json=body, headers={"X-Demo-Secret": "s3cret"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in r.text.splitlines()]
    assert [e["type"] for e in events] == ["tool", "text", "draft", "done"]
    draft_id = events[2]["draft_id"]
    assert api.posts == []  # nothing written by the chat itself
    ok = c.post("/workorders/confirm", json={"session_id": "sess-aaaaaaaa", "draft_id": draft_id}, headers={"X-Demo-Secret": "s3cret"})
    assert ok.status_code == 200 and ok.json()["id"] == "WO-00007"
    again = c.post("/workorders/confirm", json={"session_id": "sess-aaaaaaaa", "draft_id": draft_id}, headers={"X-Demo-Secret": "s3cret"})
    assert again.status_code == 404


def test_rate_limit_and_input_validation(client) -> None:
    c, _ = client
    h = {"X-Demo-Secret": "s3cret", "X-Viewer-IP": "198.51.100.9"}
    for _ in range(3):
        assert c.post("/chat", json={"message": "hi", "session_id": "sess-bbbbbbbb"}, headers=h).status_code == 200
    assert c.post("/chat", json={"message": "hi", "session_id": "sess-bbbbbbbb"}, headers=h).status_code == 429
    h2 = {"X-Demo-Secret": "s3cret", "X-Viewer-IP": "198.51.100.10"}
    assert c.post("/chat", json={"message": "", "session_id": "sess-cccccccc"}, headers=h2).status_code == 422
    assert c.post("/chat", json={"message": "hi", "session_id": "bad id!"}, headers=h2).status_code == 422
    assert c.get("/health").json()["auth"] == "X-Demo-Secret"


class DbDownPlant(FakePlant):
    """Staging with D1 over its daily limit: database-backed routes fail, readings do not."""

    def call(self, method, path, params=None, body=None):
        if path in ("/alerts", "/predictions", "/maintenance/log", "/playbook"):
            raise RuntimeError("HTTP 500: D1_ERROR daily row read limit")
        return super().call(method, path, params, body)


def test_tools_degrade_when_the_database_is_down() -> None:
    s = T.get_asset_status(DbDownPlant(), "BFP2")
    assert s["readings"]["BFP2.VIB_DE"]["value"] == 3.1  # readings still come through
    for k in ("open_alerts", "risk", "last_repair", "open_workorders"):
        assert s[k] == T.UNAVAILABLE, k  # never an empty list that reads as "none"
    assert len(s["unavailable"]) == 3
    e = T.get_events(DbDownPlant(), "BFP2")
    assert e["trend_6h"]["BFP2.VIB_DE"]["max"] == 3.3 and e["alerts"] == T.UNAVAILABLE and e["risk"] == T.UNAVAILABLE
    r = T.get_recommendations(DbDownPlant(), "bearing_wear")
    assert r["source"] == "docs/playbook/bearing_wear.md"  # local playbook fallback
