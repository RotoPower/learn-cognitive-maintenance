"""Phase 0 on staging (docs/roadmap-full.md): bring staging up to date with `main` after
the D1 free-tier reset, before `full-plant` is merged.

Steps (each safe to re-run; `--from N` resumes after a failure):

  1. delete readings past the horizon (the 2204 rows left by the old clock bug)
  2. apply plant-api migrations 0002-0004 (from a worktree of origin/main; refuses to
     apply anything else, in particular 0005, which main's ingest cannot live without)
  3. upload the GO predict artefact the scoring Worker pins (skipped if already stored)
  4. load the operator playbook with main's code (the modes staging's API knows)
  5. one forced scoring pass
  6. read the dashboard overview and print what a viewer would see

    uv run python scripts/phase0_staging.py --dry-run     # local checks + the plan, no remote call
    uv run python scripts/phase0_staging.py               # run 1-6
    uv run python scripts/phase0_staging.py --from 3      # resume at step 3

Needs `wrangler login` and PLANT_API_URL (staging) / PLANT_ADMIN_TOKEN in .env. Tokens are
passed to child processes through the environment and never printed. Never touches
production: every wrangler call names `--env staging`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plant.cli import ApiError, Client, _load_dotenv, _urllib_transport  # noqa: E402

DB = "plant-staging"
HORIZON_END = "2024-12-31T00:00:00"  # main's horizon; the full plant's is 2025-12-31
ALLOWED_MIGRATIONS = {"0002_scoring_indexes.sql", "0003_dashboard.sql", "0004_assistant.sql"}
ARTIFACT = ROOT / "models" / "artifacts" / "predict_fleet_h30_2024-01-01_s42_sym7.json"
WORKTREE = Path(tempfile.gettempdir()) / "lcm-phase0-main"
# D1 free tier: 100k rows written per day, and a DELETE writes one row per deleted row.
MAX_DELETE = 50_000
NPX = "npx.cmd" if os.name == "nt" else "npx"
PLANT_API_DIR = ROOT / "apps" / "plant-api"


def sibling_url(api_url: str, worker: str) -> str:
    """https://plant-api-staging.<sub>.workers.dev -> https://<worker>-staging.<sub>.workers.dev"""
    m = re.match(r"^(https://)plant-api-staging(\..+?)/?$", api_url)
    if not m:
        raise SystemExit(f"PLANT_API_URL is not the staging plant API (expected https://plant-api-staging.<sub>.workers.dev)")
    return f"{m.group(1)}{worker}-staging{m.group(2)}"


def pending_migrations(wrangler_output: str) -> list[str]:
    """Migration file names in the output of `wrangler d1 migrations list`."""
    return sorted(set(re.findall(r"\b\d{4}_[A-Za-z0-9_]+\.sql\b", wrangler_output)))


def run(cmd: list[str], cwd: Path, env: dict | None = None, check: bool = True, stdout_only: bool = False) -> str:
    print("  $", " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    if check and r.returncode != 0:
        print(out[-2000:])
        raise SystemExit(f"step failed: exit {r.returncode}")
    return (r.stdout or "") if stdout_only else out


def wrangler_sql(sql: str) -> list[dict]:
    out = run([NPX, "wrangler", "d1", "execute", DB, "--env", "staging", "--remote", "--json", "--command", sql], PLANT_API_DIR, stdout_only=True)
    data = json.loads(out)
    return data[0].get("results", [])


def http(method: str, url: str, token: str | None = None) -> tuple[int, object]:
    req = urllib.request.Request(url, method=method, headers={"accept": "application/json", "user-agent": "phase0-staging"})
    if token:
        req.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, body.decode("utf-8", "replace")[:500]


def main_worktree() -> Path:
    run(["git", "fetch", "origin", "main"], ROOT)
    if WORKTREE.exists():
        run(["git", "-C", str(WORKTREE), "checkout", "--detach", "origin/main"], ROOT)
    else:
        run(["git", "worktree", "add", "--detach", str(WORKTREE), "origin/main"], ROOT)
    return WORKTREE


# ------------------------------------------------------------------ steps


def step1_delete_junk(ctx) -> None:
    """delete readings past the 2024 horizon (count first; the 2204 rows)"""
    where = f"ts > '{HORIZON_END}'"
    n = wrangler_sql(f"SELECT COUNT(*) AS n FROM readings WHERE {where}")[0]["n"]  # ix_readings_ts: reads only those rows
    print(f"  {n} readings past {HORIZON_END}")
    if n > MAX_DELETE and not ctx.get("allow_bulk_delete"):
        raise SystemExit(f"refusing: deleting {n} rows writes {n} rows (daily free limit 100k); "
                         "rerun with --allow-bulk-delete to accept, or delete in batches over several days")
    if n:
        wrangler_sql(f"DELETE FROM readings WHERE {where}")
        print(f"  deleted; left: {wrangler_sql(f'SELECT COUNT(*) AS n FROM readings WHERE {where}')[0]['n']}")


def step2_migrations(ctx) -> None:
    """apply migrations 0002-0004 from origin/main (refuses 0005 or anything else)"""
    config = str(main_worktree() / "apps" / "plant-api" / "wrangler.toml")
    base = [NPX, "wrangler", "d1", "migrations"]
    pending = pending_migrations(run(base + ["list", DB, "--env", "staging", "--remote", "--config", config], PLANT_API_DIR))
    print(f"  pending: {pending or 'none'}")
    if not pending:
        return
    extra = set(pending) - ALLOWED_MIGRATIONS
    if extra:
        raise SystemExit(f"refusing: pending migrations outside 0002-0004: {sorted(extra)} (0005 waits for the full-plant merge)")
    run(base + ["apply", DB, "--env", "staging", "--remote", "--config", config], PLANT_API_DIR, env={**os.environ, "CI": "1"})
    print("  applied")


def step3_artifact(ctx) -> None:
    """upload predict_fleet_h30_2024-01-01_s42_sym7 (skipped if stored)"""
    c = Client(ctx["api"], ctx["token"], _urllib_transport)
    ids = set(c.call("GET", "/admin/model_artifacts"))  # a list of run ids
    run_id = ARTIFACT.stem
    if run_id in ids:
        print(f"  {run_id} already stored")
        return
    from plant.cli import run as plantctl

    code, out = plantctl(["--url", ctx["api"], "--admin", "upload-artifact", "--file", str(ARTIFACT)], env=os.environ)
    if code != 0:
        raise SystemExit(f"upload failed: {out[:500]}")
    print(f"  uploaded {run_id}")


def step4_playbook(ctx) -> None:
    """load the playbook with origin/main's code (3 modes)"""
    wt = main_worktree()
    env = {**os.environ, "PYTHONPATH": str(wt)}
    out = run([sys.executable, str(wt / "scripts" / "load_playbook.py"), "--url", ctx["api"]], wt, env=env)
    print("  " + "\n  ".join(out.strip().splitlines()))


def step5_score(ctx) -> None:
    """one forced scoring pass (POST /score?force=1, ~35k rows read)"""
    status, body = http("POST", sibling_url(ctx["api"], "plant-scoring") + "/score?force=1", ctx["token"])
    if status != 200:
        raise SystemExit(f"scoring returned {status}: {body}")
    b = body if isinstance(body, dict) else {}
    print(f"  as_of {b.get('as_of')}, rows_read {b.get('rows_read')}")
    print(f"  anomaly: {json.dumps(b.get('anomaly'))}")
    print(f"  predict: {json.dumps(b.get('predict'))}")


def step6_dashboard(ctx) -> None:
    """read /api/overview of the staging dashboard"""
    status, o = http("GET", sibling_url(ctx["api"], "plant-dashboard") + "/api/overview")
    if status != 200 or not isinstance(o, dict):
        raise SystemExit(f"dashboard returned {status}: {o}")
    print(f"  sim_time {o.get('sim_time')}, last scored {o.get('last_scored')}, predict {o.get('predict')}")
    print(f"  kpis {o.get('kpis')}")
    for a in o.get("assets", []):
        print(f"  {a['asset_id']:5s} {a['status']:8s} risk {a.get('risk')}  top {a.get('top_driver')}  open alerts {a.get('open_alerts')}")


STEPS = [step1_delete_junk, step2_migrations, step3_artifact, step4_playbook, step5_score, step6_dashboard]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="local checks and the plan only")
    p.add_argument("--allow-bulk-delete", action="store_true", help=f"step 1 may delete more than {MAX_DELETE} rows")
    p.add_argument("--from", dest="start", type=int, default=1, choices=range(1, len(STEPS) + 1))
    a = p.parse_args(argv)
    _load_dotenv()
    api, token = os.environ.get("PLANT_API_URL", ""), os.environ.get("PLANT_ADMIN_TOKEN")
    problems = []
    try:
        sibling_url(api, "plant-scoring")
    except SystemExit as e:
        problems.append(str(e))
    if not token:
        problems.append("PLANT_ADMIN_TOKEN is not set (.env)")
    if not ARTIFACT.exists():
        problems.append(f"missing {ARTIFACT.relative_to(ROOT).as_posix()}")
    if not (PLANT_API_DIR / "node_modules").exists():
        problems.append("apps/plant-api/node_modules missing: run npm install there")
    print("preflight:", "ok" if not problems else "")
    for msg in problems:
        print("  -", msg)
    if problems:
        return 3
    for i, fn in enumerate(STEPS, 1):
        if i < a.start:
            continue
        doc = (fn.__doc__ or fn.__name__).strip()
        print(f"\n[{i}/{len(STEPS)}] {doc}")
        if a.dry_run:
            continue
        try:
            fn({"api": api.rstrip("/"), "token": token, "allow_bulk_delete": a.allow_bulk_delete})
        except ApiError as e:
            print(f"  plant API error: {e}")
            print(f"  fix it and resume with --from {i}")
            return 1
        except SystemExit as e:
            print(f"  {e}")
            print(f"  fix it and resume with --from {i}")
            return 1
    print("\nleft for you: the Colab upload and round-trip cells (D2.3), then the 10-minute demo rehearsal"
          " (docs/claude-setup.md), with chat via scripts/run_assistant_tunnel.py and a look on a phone.")
    if WORKTREE.exists() and not a.dry_run:
        run(["git", "worktree", "remove", "--force", str(WORKTREE)], ROOT, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
