"""Pre-fill staging readings week by week, inside the D1 free tier (100k rows written per day).

D1 counts the primary-key index entry too: one reading row is 2 rows written. A new sim week is
82 tags x 168 h = 13,776 rows = ~27.6k writes, so one UTC day holds about three. Hours already stored
cost nothing, so a demo that jumps over a pre-filled week writes 0.

Per week this script (1) checks `rows_written_24h` of the staging D1 and stops if another week would
pass `--budget`, (2) jumps the staging clock one week forward, (3) drives ingest by hand (`POST /ingest`,
admin token) until every tag of the gap is stored; a cron pass fills 20 tags, so a week takes 5 passes.
The clock is left at the last stored week: Reset (dashboard or `plantctl --admin reset`) and jump again.

    uv run python scripts/prefill_staging.py --dry-run
    uv run python scripts/prefill_staging.py --to 2024-09-22T00:00:00            # at most --weeks 1
    uv run python scripts/prefill_staging.py --to 2024-09-22T00:00:00 --weeks 2

Needs `wrangler login`, PLANT_API_URL (staging) and PLANT_ADMIN_TOKEN in .env. Never touches production.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plant.cli import _load_dotenv  # noqa: E402

WEEK_WRITES = 82 * 168 * 2  # tags x hours x (row + primary-key index entry)
NPX = "npx.cmd" if os.name == "nt" else "npx"
MAX_PASSES = 12  # a week is 5 passes of 20 tags; more means something is wrong


def rows_written_24h(info: str) -> int:
    """`rows_written_24h` of `wrangler d1 info` (the number may use '.' or ',' as thousands separator)."""
    m = re.search(r"rows_written_24h\D+([\d.,]+)", info)
    if not m:
        raise ValueError("no rows_written_24h in the wrangler output")
    return int(re.sub(r"\D", "", m.group(1)))


def weeks_that_fit(written: int, budget: int, wanted: int) -> int:
    """How many of `wanted` weeks can still be written without passing `budget` rows written in 24 h."""
    return max(0, min(wanted, (budget - written) // WEEK_WRITES))


def next_target(now: str, end: str) -> str | None:
    """One week after `now`, capped at `end`; None when `now` is already there."""
    t = datetime.fromisoformat(now) + timedelta(days=7)
    e = datetime.fromisoformat(end)
    t = min(t, e)
    return None if t <= datetime.fromisoformat(now) else t.strftime("%Y-%m-%dT%H:%M:%S")


def call(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers={
        "accept": "application/json", "content-type": "application/json", "user-agent": "prefill-staging",
        "authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {url} -> HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")


def d1_written() -> int:
    r = subprocess.run([NPX, "wrangler", "d1", "info", "plant-staging", "--env", "staging"], cwd=ROOT / "apps" / "plant-api",
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return rows_written_24h((r.stdout or "") + (r.stderr or ""))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--to", default="2024-09-22T00:00:00", help="stop here (the demo reaches 2024-09-21)")
    p.add_argument("--weeks", type=int, default=1, help="weeks to fill in this run")
    p.add_argument("--budget", type=int, default=90_000, help="stop before rows written in 24 h pass this (limit 100k)")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    _load_dotenv()
    api, token = os.environ.get("PLANT_API_URL", "").rstrip("/"), os.environ.get("PLANT_ADMIN_TOKEN")
    m = re.match(r"^(https://)plant-api-staging(\..+)$", api)
    if not m or not token:
        raise SystemExit("PLANT_API_URL must be the staging plant API and PLANT_ADMIN_TOKEN must be set (.env)")
    ingest = f"{m.group(1)}plant-ingest-staging{m.group(2)}"
    written = d1_written()
    clock = call("GET", f"{api}/clock", token)
    now = clock["sim_time"]
    n = weeks_that_fit(written, a.budget, a.weeks)
    print(f"clock {now}, rows written in 24 h: {written} (budget {a.budget}, a week is ~{WEEK_WRITES}): {n} of {a.weeks} week(s) fit")
    if a.dry_run or n == 0:
        if n == 0:
            print("nothing to do now: the D1 daily write counter resets at 00:00 UTC (07:00 WIB)")
        return 0
    for _ in range(n):
        target = next_target(now, a.to)
        if not target:
            print(f"already at {a.to}")
            break
        print(f"jump {now} -> {target}")
        call("POST", f"{api}/clock/jump", token, {"to": target})
        for i in range(1, MAX_PASSES + 1):
            r = call("POST", f"{ingest}/ingest", token)
            print(f"  pass {i}: backfilled {r['backfilled_rows']} rows, tags still to do {r['backfill_tags_pending']}")
            if r["backfill_tags_pending"] == 0:
                break
        else:
            raise SystemExit("backfill did not finish: check `wrangler tail --env staging` on plant-ingest-staging")
        now = target
    print(f"stored up to {now}; D1 rows written in 24 h now {d1_written()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
