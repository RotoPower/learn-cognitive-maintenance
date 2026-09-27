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

WORKORDER_TYPES = ("inspection", "repair", "replacement", "lubrication", "other")
SEVERITIES = ("info", "warning", "critical")
DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"
PLAYBOOK_DIR = DOCS_DIR / "playbook"


def _plant_tables(text: str):
    """Assets, modes and tag units/baselines from docs/plant.md (tests/test_docs.py keeps
    that file in step with the simulator). Family rows such as `GTx.CDP` apply to every
    asset of the family; an asset's own row (`GT1.BRG_TEMP_2`) overrides it."""
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in text.splitlines() if line.lstrip().startswith("|")]
    assets = tuple(r[0].strip("`") for r in rows if re.fullmatch(r"`[A-Z]+\d`", r[0]))
    asset_modes: dict[str, list[str]] = {a: [] for a in assets}
    for r in rows:
        if len(r) >= 3 and re.fullmatch(r"`[a-z_]+`", r[0]):
            for a in (x.strip() for x in r[1].split(",")):
                asset_modes[a].append(r[0].strip("`"))
    family: dict[str, tuple[str, float]] = {}
    own: dict[str, tuple[str, float]] = {}
    dead: set[str] = set()
    for r in rows:
        m = re.fullmatch(r"`([A-Z]+)(x|\d)\.([A-Z0-9_]+)`", r[0])
        if not m or len(r) < 3:
            continue
        unit, base = r[1], float(r[2])
        if m.group(2) == "x":
            family[f"{m.group(1)}.{m.group(3)}"] = (unit, base)
        else:
            own[r[0].strip("`")] = (unit, base)
            if "dead sensor" in " ".join(r[3:]).lower():
                dead.add(r[0].strip("`"))
    info: dict[str, tuple[str, float | None]] = {"PLANT.LOAD": ("fraction", 0.75)}
    for a in assets:
        fam = a.rstrip("0123456789")
        for key, v in family.items():
            if key.split(".", 1)[0] == fam:
                info[f"{a}.{key.split('.', 1)[1]}"] = v
    info.update(own)
    for tag in dead:
        info[tag] = (info[tag][0], None)  # a constant, not a baseline to compare with
    modes = tuple(dict.fromkeys(m for ms in asset_modes.values() for m in ms))
    return assets, modes, asset_modes, info, dead


# (unit, baseline at plant load 0.75 and full health) per tag; failure modes per asset
ASSETS, MODES, ASSET_MODES, TAG_INFO, DEAD_TAGS = _plant_tables((DOCS_DIR / "plant.md").read_text(encoding="utf-8"))


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


UNAVAILABLE = "unavailable right now (plant database error); do not read this as 'none'"


def _optional(api: PlantApi, path: str, params: dict, missing: list[str]) -> Any:
    """Scoring outputs and the CMMS log live in the database; readings are computed without it.
    If the database fails, keep the readings and mark what is missing instead of failing the tool."""
    try:
        return api.call("GET", path, params=params)
    except Exception as e:
        missing.append(f"{path}: {str(e)[:120]}")
        return None


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
    missing: list[str] = []
    alerts = _optional(api, "/alerts", {"asset_id": asset, "status": "open"}, missing)
    preds = _optional(api, "/predictions", {"asset_id": asset, "limit": 2}, missing)
    log = _optional(api, "/maintenance/log", {"asset_id": asset}, missing)
    repairs = [e for e in log if e.get("kind") == "corrective_repair"] if log is not None else None
    risk = None
    if preds:
        p = preds[0]
        risk = {k: p.get(k) for k in ("as_of", "p_fail", "threshold", "alert", "drivers", "interpretation", "horizon_days")}
        risk["p_fail"] = _r(risk["p_fail"])
        if len(preds) > 1:
            risk["previous"] = {"as_of": preds[1]["as_of"], "p_fail": _r(preds[1]["p_fail"])}
    out = {
        "asset_id": asset,
        "failure_modes_watched": ASSET_MODES[asset],
        "sim_time": clock.get("sim_time"),
        "readings_at": latest.get("timestamp"),
        "readings": {tag: _reading(tag, v) for tag, v in latest.get("values", {}).items()},
        "plant_load": _reading("PLANT.LOAD", (plant.get("values") or {}).get("PLANT.LOAD")),
        "open_alerts": [_alert(a) for a in alerts] if alerts is not None else UNAVAILABLE,
        "risk": risk if risk else (UNAVAILABLE if preds is None else "no risk score for this asset (not scored yet, or not covered by the risk model)"),
        "last_repair": (repairs[-1]["timestamp"] if repairs else None) if repairs is not None else UNAVAILABLE,
        "open_workorders": [
            {k: e.get(k) for k in ("id", "type", "timestamp", "status", "description")}
            for e in log if e.get("kind") == "workorder" and e.get("status") == "open"
        ] if log is not None else UNAVAILABLE,
    }
    if missing:
        out["unavailable"] = missing
    return out


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
    missing: list[str] = []
    alerts = _optional(api, "/alerts", {"asset_id": asset, **w}, missing)
    preds = _optional(api, "/predictions", {"asset_id": asset, **w, "limit": 70}, missing)
    full_log = _optional(api, "/maintenance/log", {"asset_id": asset}, missing)
    log = [e for e in full_log if w["from"] <= str(e.get("timestamp")) <= w["to"]] if full_log is not None else None
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
    crossings = [p["as_of"] for p in sorted(preds or [], key=lambda p: p["as_of"]) if p.get("alert")]
    out = {
        "asset_id": asset,
        "window": w,
        "alerts": [_alert(a) for a in alerts] if alerts is not None else UNAVAILABLE,
        "risk": UNAVAILABLE if preds is None else {
            "scores": len(preds),
            "first": {"as_of": preds[-1]["as_of"], "p_fail": _r(preds[-1]["p_fail"])} if preds else None,
            "last": {"as_of": preds[0]["as_of"], "p_fail": _r(preds[0]["p_fail"])} if preds else None,
            "max_p_fail": _r(max(p["p_fail"] for p in preds)) if preds else None,
            "alert_days": crossings,
        },
        "maintenance": [{k: e.get(k) for k in ("kind", "id", "type", "timestamp", "status", "description")} for e in log] if log is not None else UNAVAILABLE,
        "trend_6h": trend,
    }
    if missing:
        out["unavailable"] = missing
    return out


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
    book = (_optional(api, "/playbook", {"mode": failure_mode}, []) or {}).get(failure_mode) or {}
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
