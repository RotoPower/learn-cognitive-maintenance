"""FastAPI front end over the deterministic simulator.

Design rule: the simulated clock is the only essential state. Every sensor
reading is recomputed from ``(seed, asset, tag, sim_time)`` on request, so the
API never stores a time series. The small in-memory overlays (injected faults,
work orders) are part of the *script*, not of the data; ``/admin/reset`` wipes
them.

Two bearer tokens (``Authorization: Bearer <token>`` or ``X-API-Key``):

* READ  (``PLANT_READ_TOKEN``,  default ``read-token``)  -> clock GET, plant routes
* ADMIN (``PLANT_ADMIN_TOKEN``, default ``admin-token``) -> everything, incl.
  ground truth. Only the validator and the operator hold it.

Run:  ``uv run uvicorn plant.api:app --reload``
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from plant import sim
from plant.sim import FAULT_MODES, TAGS, Plant, Scenario, canonical_asset, split_tag

ARTIFACT_DIR = Path("models/artifacts")

ASSET_INFO = {
    "GT1": "Gas turbine, 120 MW class",
    "BFP1": "Boiler feed pump A (duty), motor driven",
    "BFP2": "Boiler feed pump B (duty), motor driven",
    "CTF1": "Cooling tower fan cell 1, gearbox driven",
}

# --------------------------------------------------------------------------- #
# Simulated clock
# --------------------------------------------------------------------------- #


class SimClock:
    """sim_time = anchor_sim + (real_now - anchor_real) * speed.

    ``speed`` is sim seconds per real second: 60 -> one real second is one sim
    minute, 3600 -> one sim hour. 0 pauses the clock.
    """

    def __init__(self, sim_time: datetime, speed: float = 60.0, real_now: Callable[[], float] = time.monotonic):
        self._real_now = real_now
        self._anchor_real = real_now()
        self._anchor_sim = sim_time
        self.speed = float(speed)

    def now(self) -> datetime:
        elapsed = (self._real_now() - self._anchor_real) * self.speed
        return self._anchor_sim + timedelta(seconds=elapsed)

    def set_speed(self, speed: float) -> None:
        self._anchor_sim = self.now()
        self._anchor_real = self._real_now()
        self.speed = float(speed)

    def jump(self, to: datetime) -> None:
        self._anchor_sim = to
        self._anchor_real = self._real_now()


# --------------------------------------------------------------------------- #
# Application state
# --------------------------------------------------------------------------- #


class State:
    def __init__(
        self,
        config_path: str | Path,
        seed: int | None,
        clock_start: datetime | None,
        speed: float,
        real_now: Callable[[], float] = time.monotonic,
    ):
        self.config_path = Path(config_path)
        self.extra_scenarios: list[Scenario] = []
        self.workorders: list[dict[str, Any]] = []
        self.plant: Plant = Plant.from_yaml(self.config_path, seed=seed)
        self.clock = SimClock(clock_start or self.plant.start, speed, real_now)

    def rebuild(self, seed: int | None = None) -> None:
        base = Plant.from_yaml(self.config_path, seed=self.plant.seed if seed is None else seed)
        self.plant = Plant(
            seed=base.seed,
            start=base.start,
            horizon_days=base.horizon_h / 24.0,
            scenarios=list(base.scenarios) + self.extra_scenarios,
            outages=base.outages,
            quirks=base.quirks,
        )

    def reset(self, seed: int) -> None:
        self.extra_scenarios.clear()
        self.workorders.clear()
        self.rebuild(seed)
        self.clock.jump(self.plant.start)

    @property
    def end(self) -> datetime:
        return self.plant.to_timestamp(self.plant.horizon_h)


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class SpeedBody(BaseModel):
    speed: float = Field(ge=0, le=86_400 * 30, description="sim seconds per real second")


class JumpBody(BaseModel):
    to: datetime


class WorkOrderBody(BaseModel):
    asset_id: str
    type: Literal["inspection", "repair", "replacement", "lubrication", "other"] = "inspection"
    description: str = ""
    scheduled_for: datetime | None = None


class InjectFaultBody(BaseModel):
    asset: str
    mode: str
    onset: datetime
    duration_days: float = Field(gt=0)


class ResetBody(BaseModel):
    seed: int = 42


class ArtifactBody(BaseModel):
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    seed: int
    model: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


_INTERVAL = re.compile(r"^(\d+)([mhd])$")


def parse_interval(text: str) -> float:
    m = _INTERVAL.match(text.strip().lower())
    if not m:
        raise HTTPException(422, "interval must look like 15m, 1h or 1d")
    n, unit = int(m.group(1)), m.group(2)
    hours = n * {"m": 1 / 60, "h": 1.0, "d": 24.0}[unit]
    if hours <= 0:
        raise HTTPException(422, "interval must be positive")
    return hours


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #


def create_app(
    config_path: str | Path = sim.DEFAULT_CONFIG,
    seed: int | None = None,
    clock_start: datetime | None = None,
    speed: float = 60.0,
    read_token: str | None = None,
    admin_token: str | None = None,
    artifact_dir: Path = ARTIFACT_DIR,
    real_now: Callable[[], float] = time.monotonic,
) -> FastAPI:
    read_token = read_token or os.environ.get("PLANT_READ_TOKEN", "read-token")
    admin_token = admin_token or os.environ.get("PLANT_ADMIN_TOKEN", "admin-token")
    if clock_start is None and os.environ.get("PLANT_CLOCK_START"):
        clock_start = datetime.fromisoformat(os.environ["PLANT_CLOCK_START"])

    st = State(config_path, seed, clock_start, speed, real_now)
    app = FastAPI(title="Plant simulator API", version="0.1.0")
    app.state.sim = st

    # ----- auth ------------------------------------------------------------ #

    # HTTPBearer registers a security scheme so Swagger (/docs) shows an
    # "Authorize" button. auto_error=False keeps the X-API-Key fallback working.
    bearer = HTTPBearer(auto_error=False, description="READ or ADMIN token")

    def _token(request: Request, creds: HTTPAuthorizationCredentials | None) -> str | None:
        if creds is not None:
            return creds.credentials.strip()
        return request.headers.get("x-api-key")

    def require_read(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if _token(request, creds) not in (read_token, admin_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "READ or ADMIN token required")

    def require_admin(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if _token(request, creds) != admin_token:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "ADMIN token required")

    READ = [Depends(require_read)]
    ADMIN = [Depends(require_admin)]

    def _clamp_now(t: datetime) -> datetime:
        """No peeking into the future: cap requested times at the clock."""
        return min(t, st.clock.now())

    def _resolve_asset(asset_id: str) -> str:
        try:
            return canonical_asset(asset_id)
        except KeyError:
            raise HTTPException(404, f"unknown asset {asset_id!r}")

    # ----- index ----------------------------------------------------------- #

    @app.get("/")
    def index() -> dict[str, Any]:
        """Unauthenticated route map; interactive docs live at /docs."""
        routes = sorted(
            f"{','.join(sorted(r.methods - {'HEAD', 'OPTIONS'}))} {r.path}"
            for r in app.routes
            if getattr(r, "methods", None) and r.path not in ("/", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")
        )
        return {"service": app.title, "docs": "/docs", "auth": "Authorization: Bearer <READ|ADMIN token>", "routes": routes}

    # ----- clock ----------------------------------------------------------- #

    @app.get("/clock", dependencies=READ)
    def get_clock() -> dict[str, Any]:
        return {"sim_time": iso(st.clock.now()), "speed": st.clock.speed}

    @app.post("/clock/speed", dependencies=ADMIN)
    def set_speed(body: SpeedBody) -> dict[str, Any]:
        st.clock.set_speed(body.speed)
        return get_clock()

    @app.post("/clock/jump", dependencies=ADMIN)
    def jump(body: JumpBody) -> dict[str, Any]:
        if not (st.plant.start <= body.to <= st.end):
            raise HTTPException(422, f"sim_time must be within [{iso(st.plant.start)}, {iso(st.end)}]")
        st.clock.jump(body.to)
        return get_clock()

    # ----- plant (READ) ---------------------------------------------------- #

    @app.get("/assets", dependencies=READ)
    def assets() -> list[dict[str, Any]]:
        return [
            {"asset_id": a, "description": ASSET_INFO.get(a, ""), "tags": [f"{a}.{t}" for t in TAGS[a]]}
            for a in TAGS
        ]

    @app.get("/tags/latest", dependencies=READ)
    def latest(asset_id: str | None = None) -> dict[str, Any]:
        now = st.clock.now()
        t = now.replace(minute=0, second=0, microsecond=0)  # historian scan = top of the hour
        assets_ = [_resolve_asset(asset_id)] if asset_id else list(TAGS)
        values: dict[str, float | None] = {sim.PLANT_LOAD_TAG: st.plant.load(t)}
        for a in assets_:
            for tag in TAGS[a]:
                v = st.plant.value(a, tag, t)
                values[f"{a}.{tag}"] = None if v != v else v  # NaN -> null
        return {"timestamp": iso(t), "values": values}

    @app.get("/tags/{tag}/history", dependencies=READ)
    def history(
        tag: str,
        from_: datetime = Query(alias="from"),
        to: datetime = Query(),
        interval: str = "1h",
        max_points: int = Query(20_000, ge=1, le=100_000),
    ) -> dict[str, Any]:
        step_h = parse_interval(interval)
        if tag == sim.PLANT_LOAD_TAG:
            fn = lambda t: st.plant.load(t)  # noqa: E731
        else:
            asset, name = split_tag(tag) if "." in tag else (None, None)
            if asset is None:
                raise HTTPException(422, "tag must look like ASSET.TAG")
            asset = _resolve_asset(asset)
            if name not in TAGS[asset]:
                raise HTTPException(404, f"unknown tag {tag!r}")
            fn = lambda t, a=asset, n=name: st.plant.value(a, n, t)  # noqa: E731
        to = _clamp_now(to)
        if from_ > to:
            return {"tag": tag, "interval": interval, "points": []}
        n = int((to - from_).total_seconds() / 3600.0 / step_h) + 1
        if n > max_points:
            raise HTTPException(422, f"{n} points requested; raise max_points or coarsen interval")
        points = []
        for i in range(n):
            t = from_ + timedelta(hours=i * step_h)
            v = fn(t)
            points.append({"timestamp": iso(t), "value": None if v != v else v})
        return {"tag": tag, "interval": interval, "points": points}

    # ----- maintenance (READ) --------------------------------------------- #

    @app.get("/maintenance/log", dependencies=READ)
    def maintenance_log(asset_id: str | None = None) -> list[dict[str, Any]]:
        """CMMS history: completed corrective repairs (past only) + work orders."""
        now = st.clock.now()
        asset = _resolve_asset(asset_id) if asset_id else None
        entries: list[dict[str, Any]] = []
        for f in st.plant.failures():
            if f["repair"] <= now and (asset is None or f["asset"] == asset):
                entries.append(
                    {
                        "kind": "corrective_repair",
                        "asset_id": f["asset"],
                        "timestamp": iso(f["repair"]),
                        "description": f"Failure: {f['mode'].replace('_', ' ')}; component replaced",
                    }
                )
        for wo in st.workorders:
            if asset is None or wo["asset_id"] == asset:
                entries.append(wo)
        return sorted(entries, key=lambda e: e["timestamp"])

    @app.post("/maintenance/workorder", dependencies=READ, status_code=201)
    def create_workorder(body: WorkOrderBody) -> dict[str, Any]:
        asset = _resolve_asset(body.asset_id)
        now = st.clock.now()
        wo = {
            "kind": "workorder",
            "id": f"WO-{len(st.workorders) + 1:05d}",
            "asset_id": asset,
            "type": body.type,
            "description": body.description,
            "timestamp": iso(now),
            "scheduled_for": iso(body.scheduled_for) if body.scheduled_for else None,
            "status": "open",
        }
        st.workorders.append(wo)
        return wo

    # ----- admin (ADMIN) --------------------------------------------------- #

    @app.get("/admin/ground_truth", dependencies=ADMIN)
    def ground_truth() -> dict[str, Any]:
        gt = st.plant.ground_truth()
        now = st.clock.now()
        return {
            "seed": gt["seed"],
            "sim_time": iso(now),
            "failures": [{k: (iso(v) if isinstance(v, datetime) else v) for k, v in f.items()} for f in gt["failures"]],
            "events": [{k: (iso(v) if isinstance(v, datetime) else v) for k, v in e.items()} for e in gt["events"]],
            "health_now": {a: st.plant.health(a, now) for a in TAGS},
        }

    @app.post("/admin/inject_fault", dependencies=ADMIN, status_code=201)
    def inject_fault(body: InjectFaultBody) -> dict[str, Any]:
        asset = _resolve_asset(body.asset)
        if body.mode not in FAULT_MODES:
            raise HTTPException(422, f"unknown mode; choose from {sorted(FAULT_MODES)}")
        sc = Scenario(asset, body.mode, st.plant.to_hours(body.onset), body.duration_days * 24.0)
        st.extra_scenarios.append(sc)
        try:
            st.rebuild()
        except ValueError as exc:  # overlap with an existing scenario
            st.extra_scenarios.pop()
            raise HTTPException(409, str(exc))
        return {
            "asset": asset,
            "mode": body.mode,
            "onset": iso(st.plant.to_timestamp(sc.onset_h)),
            "failure": iso(st.plant.to_timestamp(sc.failure_h)),
        }

    @app.post("/admin/reset", dependencies=ADMIN)
    def reset(body: ResetBody) -> dict[str, Any]:
        st.reset(body.seed)
        return {"seed": st.plant.seed, **get_clock()}

    @app.post("/admin/model_artifacts", dependencies=ADMIN, status_code=201)
    def upload_artifact(body: ArtifactBody) -> dict[str, Any]:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / f"{body.run_id}.json"
        record = body.model_dump()
        record["uploaded_at"] = datetime.now(UTC).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
        record["sim_time"] = iso(st.clock.now())
        path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        return {"run_id": body.run_id, "path": path.as_posix(), "bytes": path.stat().st_size}

    @app.get("/admin/model_artifacts", dependencies=ADMIN)
    def list_artifacts() -> list[str]:
        if not artifact_dir.exists():
            return []
        return sorted(p.stem for p in artifact_dir.glob("*.json"))

    return app


app = create_app()
