"""plantctl — small CLI over the plant API, for humans and subagents.

    uv run plantctl assets
    uv run plantctl clock
    uv run plantctl latest --asset BFP-2
    uv run plantctl history --tag BFP2.VIB_DE --from 2024-08-01 --to 2024-08-31 [--interval 1h] [--csv]
    uv run plantctl maintenance-log [--asset BFP2]
    uv run plantctl workorder --asset BFP2 --type inspection --description "vib check"

    uv run plantctl --admin ground-truth          # validator only
    uv run plantctl --admin inject-fault --asset BFP1 --mode bearing_wear --onset 2024-11-01 --duration-days 10
    uv run plantctl --admin reset --seed 42
    uv run plantctl --admin jump --to 2024-09-01
    uv run plantctl --admin speed --speed 3600
    uv run plantctl --admin artifacts
    uv run plantctl --admin upload-artifact --file models/artifacts/<run_id>.json

Config from the environment (a ``.env`` in the repo root is loaded if present):
    PLANT_API_URL      default http://127.0.0.1:8000
    PLANT_READ_TOKEN   default read-token
    PLANT_ADMIN_TOKEN  used only with --admin

Output is JSON on stdout (``--table`` for a readable view). Admin routes are
only reached when ``--admin`` is given explicitly, so a hook can police
subagents by matching ``plantctl --admin`` in the Bash command.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

def _load_dotenv() -> None:
    """Load a repo-root .env for real invocations only (never at import time,
    so tests and other modules do not inherit tokens by accident)."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # pragma: no cover
        pass


DEFAULT_URL = "http://127.0.0.1:8000"
Transport = Callable[[str, str, dict | None, str | None], tuple[int, Any]]


class ApiError(Exception):
    def __init__(self, status: int, detail: Any):
        super().__init__(f"HTTP {status}: {detail}")
        self.status, self.detail = status, detail


def _urllib_transport(method: str, url: str, body: dict | None, token: str | None) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, default=str).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except urllib.error.URLError as e:
        raise ConnectionError(f"cannot reach plant API at {url}: {e.reason}. Start it with `uv run uvicorn plant.api:app`") from None


class Client:
    def __init__(self, base_url: str, token: str | None, transport: Transport = _urllib_transport):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport

    def call(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> Any:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        status, payload = self.transport(method, url, body, self.token)
        if status >= 400:
            raise ApiError(status, payload.get("detail", payload) if isinstance(payload, dict) else payload)
        return payload


# ------------------------------------------------------------------ commands


def cmd_clock(c: Client, a) -> Any:
    return c.call("GET", "/clock")


def cmd_assets(c: Client, a) -> Any:
    return c.call("GET", "/assets")


def cmd_latest(c: Client, a) -> Any:
    return c.call("GET", "/tags/latest", {"asset_id": a.asset})


def cmd_history(c: Client, a) -> Any:
    return c.call("GET", f"/tags/{a.tag}/history", {"from": a.from_, "to": a.to, "interval": a.interval, "max_points": a.max_points})


def cmd_maintenance_log(c: Client, a) -> Any:
    return c.call("GET", "/maintenance/log", {"asset_id": a.asset})


def cmd_workorder(c: Client, a) -> Any:
    body = {"asset_id": a.asset, "type": a.type, "description": a.description}
    if a.scheduled_for:
        body["scheduled_for"] = a.scheduled_for
    return c.call("POST", "/maintenance/workorder", body=body)


def cmd_ground_truth(c: Client, a) -> Any:
    return c.call("GET", "/admin/ground_truth")


def cmd_inject_fault(c: Client, a) -> Any:
    return c.call("POST", "/admin/inject_fault", body={"asset": a.asset, "mode": a.mode, "onset": a.onset, "duration_days": a.duration_days})


def cmd_reset(c: Client, a) -> Any:
    return c.call("POST", "/admin/reset", body={"seed": a.seed})


def cmd_jump(c: Client, a) -> Any:
    return c.call("POST", "/clock/jump", body={"to": a.to})


def cmd_speed(c: Client, a) -> Any:
    return c.call("POST", "/clock/speed", body={"speed": a.speed})


def cmd_artifacts(c: Client, a) -> Any:
    return c.call("GET", "/admin/model_artifacts")


def cmd_upload_artifact(c: Client, a) -> Any:
    art = json.loads(Path(a.file).read_text(encoding="utf-8"))
    body = art if {"run_id", "seed"} <= set(art) and "model" in art else {
        "run_id": art["run_id"],
        "seed": art.get("seed", 0),
        "model": {k: art[k] for k in ("feature_names", "scaler", "coefficients", "intercept", "threshold", "config") if k in art},
        "metrics": art.get("metrics", {}),
        "meta": {k: art[k] for k in ("task", "target", "horizon_days", "cutoff", "split", "created_at", "as_of") if k in art},
    }
    return c.call("POST", "/admin/model_artifacts", body=body)


ADMIN_COMMANDS = {"ground-truth", "inject-fault", "reset", "jump", "speed", "artifacts", "upload-artifact"}


# -------------------------------------------------------------------- output


def _table(payload: Any) -> str:
    if isinstance(payload, dict) and "points" in payload:  # history
        rows = [(p["timestamp"], "" if p["value"] is None else f"{p['value']:.4f}") for p in payload["points"]]
        return f"{payload['tag']} ({payload['interval']}), {len(rows)} points\n" + "\n".join(f"{t}  {v}" for t, v in rows)
    if isinstance(payload, dict) and "values" in payload:  # latest
        return f"as of {payload['timestamp']}\n" + "\n".join(
            f"{k:22s} {'null' if v is None else f'{v:.4f}'}" for k, v in payload["values"].items()
        )
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        cols = list(dict.fromkeys(k for row in payload for k in row))
        widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in payload)) for c in cols}
        head = "  ".join(c.ljust(widths[c]) for c in cols)
        body = "\n".join("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols) for r in payload)
        return head + "\n" + body
    return json.dumps(payload, indent=2, default=str)


def _csv(payload: Any) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    if isinstance(payload, dict) and "points" in payload:
        w.writerow(["timestamp", payload["tag"]])
        for p in payload["points"]:
            w.writerow([p["timestamp"], "" if p["value"] is None else p["value"]])
    elif isinstance(payload, dict) and "values" in payload:
        w.writerow(["timestamp", "tag", "value"])
        for k, v in payload["values"].items():
            w.writerow([payload["timestamp"], k, "" if v is None else v])
    elif isinstance(payload, list) and payload and isinstance(payload[0], dict):
        cols = list(dict.fromkeys(k for row in payload for k in row))
        w.writerow(cols)
        for r in payload:
            w.writerow([r.get(c, "") for c in cols])
    else:
        raise SystemExit("--csv is only for history, latest and list outputs")
    return buf.getvalue()


# ---------------------------------------------------------------------- main


def _add_globals(p: argparse.ArgumentParser, suppress: bool) -> None:
    """Global flags, accepted both before and after the subcommand."""
    d = argparse.SUPPRESS if suppress else None
    f = argparse.SUPPRESS if suppress else False
    p.add_argument("--url", default=d, help="API base url (env PLANT_API_URL)")
    p.add_argument("--admin", action="store_true", default=f, help="use PLANT_ADMIN_TOKEN; required for admin commands")
    p.add_argument("--table", action="store_true", default=f, help="readable output instead of JSON")
    p.add_argument("--csv", action="store_true", default=f, help="CSV output (history, latest, lists)")
    p.add_argument("--out", default=d, help="write output to this file instead of stdout (never data/raw)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="plantctl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_globals(p, suppress=False)
    common = argparse.ArgumentParser(add_help=False)
    _add_globals(common, suppress=True)
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=lambda **kw: argparse.ArgumentParser(parents=[common], **kw))

    sub.add_parser("clock", help="current sim time and speed").set_defaults(fn=cmd_clock)
    sub.add_parser("assets", help="assets and their tags").set_defaults(fn=cmd_assets)
    s = sub.add_parser("latest", help="latest scan of one asset or the fleet")
    s.add_argument("--asset", default=None)
    s.set_defaults(fn=cmd_latest)
    s = sub.add_parser("history", help="time series for one tag")
    s.add_argument("--tag", required=True, help="e.g. BFP2.VIB_DE")
    s.add_argument("--from", dest="from_", required=True)
    s.add_argument("--to", required=True)
    s.add_argument("--interval", default="1h")
    s.add_argument("--max-points", type=int, default=20000)
    s.set_defaults(fn=cmd_history)
    s = sub.add_parser("maintenance-log", help="past repairs and work orders")
    s.add_argument("--asset", default=None)
    s.set_defaults(fn=cmd_maintenance_log)
    s = sub.add_parser("workorder", help="raise a work order")
    s.add_argument("--asset", required=True)
    s.add_argument("--type", default="inspection", choices=["inspection", "repair", "replacement", "lubrication", "other"])
    s.add_argument("--description", default="")
    s.add_argument("--scheduled-for", default=None)
    s.set_defaults(fn=cmd_workorder)

    sub.add_parser("ground-truth", help="[admin] scripted failures, events, current health").set_defaults(fn=cmd_ground_truth)
    s = sub.add_parser("inject-fault", help="[admin] add a degradation scenario")
    s.add_argument("--asset", required=True)
    s.add_argument("--mode", required=True)
    s.add_argument("--onset", required=True)
    s.add_argument("--duration-days", type=float, required=True)
    s.set_defaults(fn=cmd_inject_fault)
    s = sub.add_parser("reset", help="[admin] new seed, clock to start, clear injected faults")
    s.add_argument("--seed", type=int, default=42)
    s.set_defaults(fn=cmd_reset)
    s = sub.add_parser("jump", help="[admin] set the sim clock")
    s.add_argument("--to", required=True)
    s.set_defaults(fn=cmd_jump)
    s = sub.add_parser("speed", help="[admin] sim seconds per real second")
    s.add_argument("--speed", type=float, required=True)
    s.set_defaults(fn=cmd_speed)
    sub.add_parser("artifacts", help="[admin] list uploaded model artefacts").set_defaults(fn=cmd_artifacts)
    s = sub.add_parser("upload-artifact", help="[admin] POST an artefact JSON")
    s.add_argument("--file", required=True)
    s.set_defaults(fn=cmd_upload_artifact)
    return p


def run(argv: list[str] | None, transport: Transport = _urllib_transport, env: dict | None = None) -> tuple[int, str]:
    """Parse, call, format. Returns (exit_code, output_text). Testable without a server."""
    if env is None:
        _load_dotenv()
        env = os.environ
    a = build_parser().parse_args(argv)
    if a.cmd in ADMIN_COMMANDS and not a.admin:
        return 3, f"plantctl: '{a.cmd}' is an admin command; pass --admin (validator only)"
    if a.admin:
        token = env.get("PLANT_ADMIN_TOKEN")
        if not token:
            return 3, "plantctl: --admin needs PLANT_ADMIN_TOKEN in the environment"
    else:
        token = env.get("PLANT_READ_TOKEN", "read-token")
    client = Client(a.url or env.get("PLANT_API_URL", DEFAULT_URL), token, transport)
    try:
        payload = a.fn(client, a)
    except ApiError as e:
        return 1, f"plantctl: {e}"
    except ConnectionError as e:
        return 2, f"plantctl: {e}"
    text = _csv(payload) if a.csv else _table(payload) if a.table else json.dumps(payload, indent=2, default=str)
    if a.out:
        out = Path(a.out)
        if "raw" in out.parts:
            return 3, "plantctl: refusing to write under data/raw (read-only)"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        return 0, f"wrote {out.as_posix()}"
    return 0, text


def main(argv: list[str] | None = None) -> None:
    code, text = run(argv)
    print(text, file=sys.stdout if code == 0 else sys.stderr)
    sys.exit(code)


if __name__ == "__main__":
    main()
