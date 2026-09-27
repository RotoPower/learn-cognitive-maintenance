"""The assistant's four tools as plain functions over the plant API (read token).

Design rule of the assistant: it never guesses numbers. Every fact it states comes
from one of these functions, which return compact JSON-able dicts with units, the sim
time of each value, and baselines from docs/plant.md so the model can say "3.1 mm/s
against a 1.8 mm/s baseline" without inventing either number.

`create_workorder` never writes: it stores a draft that only the operator's Confirm
(POST /workorders/confirm in app.py, outside the model) turns into a work order.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

ASSETS = ("GT1", "BFP1", "BFP2", "CTF1")
MODES = ("bearing_wear", "compressor_fouling", "gearbox_wear")
ASSET_MODE = {"GT1": "compressor_fouling", "BFP1": "bearing_wear", "BFP2": "bearing_wear", "CTF1": "gearbox_wear"}
WORKORDER_TYPES = ("inspection", "repair", "replacement", "lubrication", "other")
SEVERITIES = ("info", "warning", "critical")
PLAYBOOK_DIR = Path(__file__).resolve().parents[3] / "docs" / "playbook"

# (unit, baseline at plant load 0.75 and full health) from docs/plant.md
TAG_INFO: dict[str, tuple[str, float | None]] = {
    "PLANT.LOAD": ("fraction", 0.75),
    "GT1.LOAD_MW": ("MW", 90), "GT1.EXH_TEMP": ("degC", 540), "GT1.CDP": ("bar", 15.5), "GT1.FUEL_FLOW": ("kg/s", 6.2),
    "GT1.VIB_1": ("mm/s", 2.1), "GT1.BRG_TEMP_1": ("degC", 78), "GT1.BRG_TEMP_2": ("degC", None),  # dead sensor
    "CTF1.SPEED": ("rpm", 118), "CTF1.VIB": ("mm/s", 2.4), "CTF1.GBX_OIL_TEMP": ("degC", 58), "CTF1.MOTOR_CURR": ("A", 95),
}
for _p in ("BFP1", "BFP2"):
    TAG_INFO.update({
        f"{_p}.FLOW": ("t/h", 260), f"{_p}.DISCH_PRESS": ("bar", 165), f"{_p}.VIB_DE": ("mm/s", 1.8),
        f"{_p}.VIB_NDE": ("mm/s", 1.5), f"{_p}.BRG_TEMP_DE": ("degC", 62), f"{_p}.MOTOR_CURR": ("A", 310),
    })
DEAD_TAGS = {"GT1.BRG_TEMP_2"}


class PlantApi(Protocol):
    def call(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> Any: ...


class ToolError(ValueError):
    """Bad input the model should correct (unknown asset, bad date...)."""


def _asset(asset_id: str) -> str:
    a = str(asset_id).upper().replace("-", "").strip()
    if a not in ASSETS:
        raise ToolError(f"unknown asset '{asset_id}'; use one of {', '.join(ASSETS)}")
    return a


def _ts(s: str | None, name: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", ""))
    except ValueError:
        raise ToolError(f"{name}: expected an ISO time like 2024-09-20T00:00:00, got '{s}'") from None


def _r(v: float | None, nd: int = 3) -> float | None:
    return None if v is None else round(float(v), nd)


def _reading(tag: str, value: float | None) -> dict:
    unit, base = TAG_INFO.get(tag, ("", None))
    out: dict[str, Any] = {"value": _r(value), "unit": unit}
    if tag in DEAD_TAGS:
        out["note"] = "dead sensor: reads a constant, ignore"
    elif base is not None:
        out["baseline"] = base
        if value is not None and tag != "PLANT.LOAD":
            out["vs_baseline_pct"] = _r(100 * (value - base) / base, 1)
    if value is None:
        out["note"] = "no reading (sensor outage)"
    return out


# ------------------------------------------------------------------ tool 1


def get_asset_status(api: PlantApi, asset_id: str) -> dict:
    asset = _asset(asset_id)
    clock = api.call("GET", "/clock")
    latest = api.call("GET", "/tags/latest", params={"asset_id": asset})
    plant = api.call("GET", "/tags/latest")  # the fleet scan carries PLANT.LOAD
    alerts = api.call("GET", "/alerts", params={"asset_id": asset, "status": "open"})
    preds = api.call("GET", "/predictions", params={"asset_id": asset, "limit": 2})
    log = api.call("GET", "/maintenance/log", params={"asset_id": asset})
    repairs = [e for e in log if e.get("kind") == "corrective_repair"]
    risk = None
    if preds:
        p = preds[0]
        risk = {k: p.get(k) for k in ("as_of", "p_fail", "threshold", "alert", "drivers", "interpretation", "horizon_days")}
        risk["p_fail"] = _r(risk["p_fail"])
        if len(preds) > 1:
            risk["previous"] = {"as_of": preds[1]["as_of"], "p_fail": _r(preds[1]["p_fail"])}
    return {
        "asset_id": asset,
        "failure_mode_watched": ASSET_MODE[asset],
        "sim_time": clock.get("sim_time"),
        "readings_at": latest.get("timestamp"),
        "readings": {tag: _reading(tag, v) for tag, v in latest.get("values", {}).items()},
        "plant_load": _reading("PLANT.LOAD", (plant.get("values") or {}).get("PLANT.LOAD")),
        "open_alerts": [_alert(a) for a in alerts],
        "risk": risk if risk else "no risk score yet (scoring has not run for this asset)",
        "last_repair": repairs[-1]["timestamp"] if repairs else None,
        "open_workorders": [
            {k: e.get(k) for k in ("id", "type", "timestamp", "status", "description")}
            for e in log if e.get("kind") == "workorder" and e.get("status") == "open"
        ],
    }


def _alert(a: dict) -> dict:
    return {
        "tag": a.get("tag") or a.get("kind"), "kind": a.get("kind"), "from": a.get("first_flag_ts"), "to": a.get("last_flag_ts"),
        "peak": _r(a.get("severity"), 2), "peak_is": "z-score" if a.get("kind") == "anomaly" else "failure probability",
        "reading": a.get("interpretation"), "status": a.get("status"),
    }


# ------------------------------------------------------------------ tool 2


def get_events(api: PlantApi, asset_id: str, from_: str | None = None, to: str | None = None) -> dict:
    asset = _asset(asset_id)
    now = _ts(api.call("GET", "/clock")["sim_time"], "sim_time")
    t1 = min(_ts(to, "to") or now, now)
    t0 = _ts(from_, "from") or t1 - timedelta(days=7)
    if t0 >= t1:
        raise ToolError("'from' must be before 'to' (and before the current sim time)")
    if (t1 - t0) > timedelta(days=62):
        raise ToolError("window too long: 62 days at most")
    w = {"from": t0.isoformat(), "to": t1.isoformat()}
    alerts = api.call("GET", "/alerts", params={"asset_id": asset, **w})
    preds = api.call("GET", "/predictions", params={"asset_id": asset, **w, "limit": 70})
    log = [e for e in api.call("GET", "/maintenance/log", params={"asset_id": asset}) if w["from"] <= str(e.get("timestamp")) <= w["to"]]
    assets = api.call("GET", "/assets")
    tags = next((a["tags"] for a in assets if a["asset_id"] == asset), [])
    trend = {}
    for tag in tags:
        if tag in DEAD_TAGS:
            continue
        pts = api.call("GET", f"/tags/{tag}/history", params={**w, "interval": "6h"})["points"]
        vals = [(p["timestamp"], p["value"]) for p in pts if p["value"] is not None]
        if not vals:
            continue
        hi = max(vals, key=lambda x: x[1])
        unit, base = TAG_INFO.get(tag, ("", None))
        trend[tag] = {"unit": unit, "baseline": base, "start": _r(vals[0][1]), "end": _r(vals[-1][1]),
                      "max": _r(hi[1]), "max_at": hi[0], "samples": len(vals)}
    crossings = [p["as_of"] for p in sorted(preds, key=lambda p: p["as_of"]) if p.get("alert")]
    return {
        "asset_id": asset,
        "window": w,
        "alerts": [_alert(a) for a in alerts],
        "risk": {
            "scores": len(preds),
            "first": {"as_of": preds[-1]["as_of"], "p_fail": _r(preds[-1]["p_fail"])} if preds else None,
            "last": {"as_of": preds[0]["as_of"], "p_fail": _r(preds[0]["p_fail"])} if preds else None,
            "max_p_fail": _r(max(p["p_fail"] for p in preds)) if preds else None,
            "alert_days": crossings,
        },
        "maintenance": [{k: e.get(k) for k in ("kind", "id", "type", "timestamp", "status", "description")} for e in log],
        "trend_6h": trend,
    }


# ------------------------------------------------------------------ tool 3


def _local_playbook(mode: str) -> dict[str, str]:
    """Fallback when the API has no playbook loaded: parse docs/playbook/<mode>.md."""
    heads = {"symptoms": "symptoms", "confirming checks": "checks", "immediate actions": "actions",
             "spare parts": "spares", "typical lead time": "lead_time"}
    path = PLAYBOOK_DIR / f"{mode}.md"
    if not path.exists():
        return {}
    out, key, lines = {}, None, []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if key:
                out[key] = "\n".join(lines).strip()
            key = next((v for k, v in heads.items() if m.group(1).lower().startswith(k)), None)
            lines = []
        elif key:
            lines.append(line)
    if key:
        out[key] = "\n".join(lines).strip()
    return out


def get_recommendations(api: PlantApi, failure_mode: str, severity: str = "warning", query: str | None = None) -> dict:
    if failure_mode not in MODES:
        raise ToolError(f"unknown failure mode '{failure_mode}'; use one of {', '.join(MODES)}")
    if severity not in SEVERITIES:
        raise ToolError(f"severity must be one of {', '.join(SEVERITIES)}")
    book = (api.call("GET", "/playbook", params={"mode": failure_mode}) or {}).get(failure_mode) or {}
    source = "playbook (D1)"
    if not book:
        book, source = _local_playbook(failure_mode), f"docs/playbook/{failure_mode}.md"
    if not book:
        raise ToolError(f"no playbook for {failure_mode}")
    order = ["actions", "checks", "symptoms", "spares", "lead_time"] if severity == "critical" else ["symptoms", "checks", "actions", "spares", "lead_time"]
    sections = {k: book[k] for k in order if k in book}
    if query:
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
        hits = {k: "\n".join(line for line in v.splitlines() if any(w in line.lower() for w in words)) for k, v in sections.items()}
        hits = {k: v for k, v in hits.items() if v}
        if hits:
            sections = hits
    guidance = {
        "critical": "Critical: lead with the stop criteria and immediate actions; raise a work order after confirming.",
        "warning": "Warning: confirm with the checks first, then plan the actions into a maintenance window.",
        "info": "Info: keep watching the symptom tags; no action yet.",
    }[severity]
    return {"failure_mode": failure_mode, "severity": severity, "source": source, "guidance": guidance, "sections": sections}


# ------------------------------------------------------------------ tool 4: drafts only


@dataclass
class Draft:
    draft_id: str
    session_id: str
    asset_id: str
    type: str
    description: str
    scheduled_for: str | None
    created: float = field(default_factory=time.monotonic)


class DraftStore:
    """Work-order drafts awaiting the operator's Confirm. Single use, expire after `ttl` s."""

    def __init__(self, ttl: float = 1800):
        self.ttl = ttl
        self._drafts: dict[str, Draft] = {}

    def add(self, d: Draft) -> Draft:
        self._purge()
        self._drafts[d.draft_id] = d
        return d

    def take(self, draft_id: str, session_id: str) -> Draft | None:
        self._purge()
        d = self._drafts.get(draft_id)
        if d is None or d.session_id != session_id:
            return None
        return self._drafts.pop(draft_id)

    def created_since(self, session_id: str, t: float) -> list[Draft]:
        return [d for d in self._drafts.values() if d.session_id == session_id and d.created >= t]

    def _purge(self) -> None:
        now = time.monotonic()
        for k in [k for k, d in self._drafts.items() if now - d.created > self.ttl]:
            del self._drafts[k]


def create_workorder(store: DraftStore, session_id: str, asset_id: str, type: str = "inspection",
                     description: str = "", scheduled_for: str | None = None) -> dict:
    asset = _asset(asset_id)
    if type not in WORKORDER_TYPES:
        raise ToolError(f"type must be one of {', '.join(WORKORDER_TYPES)}")
    description = str(description).strip()[:500]
    if not description:
        raise ToolError("description is required: say what the crew should do and why (tag, value, time)")
    if scheduled_for:
        _ts(scheduled_for, "scheduled_for")
    d = store.add(Draft(secrets.token_urlsafe(9), session_id, asset, type, description, scheduled_for))
    return {
        "draft_id": d.draft_id,
        "status": "awaiting_operator_confirmation",
        "work_order": {"asset_id": asset, "type": type, "description": description, "scheduled_for": scheduled_for},
        "note": "Not created yet. The operator must press Confirm in the chat panel; you cannot confirm it.",
    }


def confirm_workorder(store: DraftStore, api: PlantApi, session_id: str, draft_id: str) -> dict:
    """Called by the UI's Confirm button (app.py), never by the model."""
    d = store.take(draft_id, session_id)
    if d is None:
        raise ToolError("no such draft for this session (already confirmed, expired, or never drafted)")
    body = {"asset_id": d.asset_id, "type": d.type, "description": f"[assistant] {d.description}"}
    if d.scheduled_for:
        body["scheduled_for"] = d.scheduled_for
    return api.call("POST", "/maintenance/workorder", body=body)
