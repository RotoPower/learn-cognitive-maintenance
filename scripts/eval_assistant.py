"""Score the running assistant against the golden set (module E3.5). Run after every prompt change.

    uv run python scripts/eval_assistant.py                          # all scenarios
    uv run python scripts/eval_assistant.py --scenario bfp2_bearing_wear
    uv run python scripts/eval_assistant.py --ingest-url http://127.0.0.1:8788 --scoring-url http://127.0.0.1:8789

Per scenario: set the plant clock to `as_of` (admin API), optionally backfill readings and run
scoring, then ask the questions in one chat session. Each answer is graded on:
  1. failure mode / facts: the expected keyword groups match and no forbidden claim appears;
  2. numbers: every number in the answer appears in the tool outputs at that sim time
     (recomputed here with the assistant's own tool functions), so nothing is invented;
  3. playbook: "what should we do" answers carry at least two of the playbook's actions;
  4. drafts: work-order requests produce a draft and never claim the order exists.
Writes reports/eval_assistant_<timestamp>.md. Costs one assistant turn per question.

Env (.env): PLANT_API_URL, PLANT_READ_TOKEN, PLANT_ADMIN_TOKEN; ASSISTANT_URL (default
http://127.0.0.1:8100) and ASSISTANT_SECRET when the assistant requires it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "assistant"))
from assistant import tools as T  # noqa: E402  (the same functions the assistant calls)
from plant.cli import Client, _load_dotenv  # noqa: E402

GOLDEN = Path("tests/golden/assistant.yaml")
NUM = re.compile(r"(?<![\w.:-])[-+]?\d+(?:[.,]\d+)?(?![\w:-])")


# ------------------------------------------------------------------ grading


def numbers(text: str) -> list[float]:
    """Numbers worth checking: drop dates, clock times, list markers, and small counts."""
    text = re.sub(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?(?:\s*[/–-]\s*\d{1,2}\b)?", " ", text)  # 2024-07-18/19
    text = re.sub(r"\b\d{1,2}(?:\s*[–-]\s*\d{1,2})?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b", " ", text)
    text = re.sub(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b", " ", text)
    text = re.sub(r"\b\d{1,2}:\d{2}\b", " ", text)
    text = re.sub(r"(?m)^\s*\d+[.)]\s", " ", text)
    out = []
    for m in NUM.finditer(text):
        v = float(m.group().replace(",", "."))
        if abs(v) < 10 and float(v).is_integer():
            continue  # "3 alerts", "7 days", "2 pumps": counts, not measurements
        out.append(v)
    return out


def facts(obj, acc: set[float] | None = None) -> set[float]:
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        for v in obj.values():
            facts(v, acc)
        if isinstance(obj.get("value"), (int, float)) and isinstance(obj.get("baseline"), (int, float)) and obj["baseline"]:
            acc.update({obj["value"] / obj["baseline"], obj["value"] - obj["baseline"]})  # "1.7x", "+1.3 mm/s"
    elif isinstance(obj, list):
        for v in obj:
            facts(v, acc)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        acc.update({float(obj), float(obj) * 100})  # probabilities quoted as percent
    elif isinstance(obj, str):
        acc.update(numbers(obj))
    return acc


def matches(v: float, known: set[float]) -> bool:
    for k in known:
        tol = max(abs(k) * 0.011, 0.051 if abs(k) < 10 else 0.51)  # rounding to what was shown
        if abs(abs(v) - abs(k)) <= tol:
            return True
    return False


def playbook_hits(answer: str, actions: str) -> int:
    a, hits = answer.lower(), 0
    for bullet in [b.strip("- ").lower() for b in actions.splitlines() if b.strip()]:
        words = {w for w in re.findall(r"[a-z_]{5,}", bullet)} - {"which", "their", "there", "about", "above", "before"}
        if sum(w in a for w in words) >= 2:
            hits += 1
    return hits


def grade(q: dict, answer: str, drafts: list, known: set[float], playbook: dict) -> dict:
    low = answer.lower()
    missing = [g for g in q.get("expect", []) if not any(k.lower() in low for k in g)]
    forbidden = [f for f in q.get("forbid", []) if f.lower() in low]
    invented = [v for v in numbers(answer) if not matches(v, known)]
    res = {"facts": not missing and not forbidden, "numbers": not invented, "missing": missing, "forbidden": forbidden, "invented": invented}
    if q.get("playbook"):
        hits = playbook_hits(answer, (playbook.get(q["playbook"]) or {}).get("actions", ""))
        res["playbook"] = hits >= 2
        res["playbook_hits"] = hits
    if q.get("draft"):
        res["draft"] = bool(drafts)
    res["pass"] = all(v for k, v in res.items() if k in ("facts", "numbers", "playbook", "draft"))
    return res


# ------------------------------------------------------------------ plumbing


def post(url: str, body: dict | None, headers: dict, timeout: float = 300) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"content-type": "application/json", "user-agent": "eval-assistant/0.1", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def prepare(url: str, headers: dict, attempts: int = 3) -> None:
    """Ingest / scoring before a scenario. Local Workers share one SQLite file, so a call right
    after a large ingest can hit a busy database: retry, and say why if it keeps failing."""
    for i in range(attempts):
        try:
            post(url, None, headers)
            return
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            print(f"  {url} -> HTTP {e.code} (attempt {i + 1}/{attempts}): {detail}")
            if i + 1 == attempts:
                raise SystemExit(f"could not prepare the scenario: {url}")
            time.sleep(5 * (i + 1))


def ask(assistant: str, secret: str | None, session: str, message: str) -> tuple[str, list, list]:
    raw = post(f"{assistant}/chat", {"message": message, "session_id": session}, {"x-demo-secret": secret} if secret else {})
    text, tools, drafts = [], [], []
    for line in raw.decode("utf-8").splitlines():
        ev = json.loads(line)
        if ev["type"] == "text":
            text.append(ev["text"])
        elif ev["type"] == "tool":
            tools.append(ev["name"])
        elif ev["type"] == "draft":
            drafts.append(ev)
        elif ev["type"] == "error":
            text.append(f"[error: {ev['message']}]")
    return "".join(text).strip(), tools, drafts


def tool_facts(api: Client, asset: str) -> set[float]:
    """Everything the tools could have told the assistant at this sim time: every asset's status
    (it compares pumps), the asked asset's events over the windows it tends to pick, all playbooks."""
    known: set[float] = set()
    now = datetime.fromisoformat(api.call("GET", "/clock")["sim_time"])
    calls = [lambda a=a: T.get_asset_status(api, a) for a in T.ASSETS]
    calls += [lambda d=d: T.get_events(api, asset, (now - timedelta(days=d)).isoformat()) for d in (1, 3, 7, 14, 30)]
    calls += [lambda m=m: T.get_recommendations(api, m) for m in T.MODES]
    for fn in calls:
        try:
            facts(fn(), known)
        except Exception:
            pass
    return known


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--golden", default=str(GOLDEN))
    p.add_argument("--scenario", action="append", help="only these scenarios (repeatable)")
    p.add_argument("--assistant", default=None)
    p.add_argument("--ingest-url", default=None, help="POST /ingest after each clock jump (backfill readings)")
    p.add_argument("--scoring-url", default=None, help="POST /score?force=1 after each clock jump")
    p.add_argument("--report-dir", default="reports")
    a = p.parse_args(argv)
    _load_dotenv()
    base, read, admin = os.environ.get("PLANT_API_URL", "http://127.0.0.1:8000"), os.environ.get("PLANT_READ_TOKEN"), os.environ.get("PLANT_ADMIN_TOKEN")
    assistant = (a.assistant or os.environ.get("ASSISTANT_URL") or "http://127.0.0.1:8100").rstrip("/")
    secret = os.environ.get("ASSISTANT_SECRET") or None
    api, adm = Client(base, read), Client(base, admin)
    golden = yaml.safe_load(Path(a.golden).read_text(encoding="utf-8"))
    playbook = api.call("GET", "/playbook") or {m: T._local_playbook(m) for m in T.MODES}
    run = datetime.now().strftime("%Y%m%d-%H%M%S")
    rows, lines = [], [f"# Assistant eval {run}", "", f"Assistant `{assistant}`, plant `{base}`, golden `{a.golden}`.", ""]

    for sc in golden["scenarios"]:
        if a.scenario and sc["name"] not in a.scenario:
            continue
        adm.call("POST", "/clock/speed", body={"speed": 0})
        adm.call("POST", "/clock/jump", body={"to": sc["as_of"]})
        hdr = {"authorization": f"Bearer {admin}"}
        if a.ingest_url:
            prepare(f"{a.ingest_url.rstrip('/')}/ingest", hdr)
        if a.scoring_url:
            prepare(f"{a.scoring_url.rstrip('/')}/score?force=1", hdr)
        session = f"eval-{sc['name'][:40]}-{int(time.time())}"
        lines += [f"## {sc['name']} (as of {sc['as_of']})", ""]
        for i, q in enumerate(sc["questions"], 1):
            asset = next((x for x in T.ASSETS if x in q["q"].replace("-", "")), None) or \
                {"gas turbine": "GT1", "cooling tower": "CTF1", "fan": "CTF1"}.get(next((k for k in ("gas turbine", "cooling tower", "fan") if k in q["q"].lower()), ""), "BFP2")
            t0 = time.time()
            answer, tools, drafts = ask(assistant, secret, session, q["q"])
            known = tool_facts(api, asset) | set(numbers(q["q"]))
            g = grade(q, answer, drafts, known, playbook)
            rows.append({"scenario": sc["name"], **g})
            mark = "PASS" if g["pass"] else "FAIL"
            print(f"{mark} {sc['name']} q{i} ({time.time() - t0:.0f}s, tools {tools}) "
                  + ("" if g["pass"] else json.dumps({k: g[k] for k in ("missing", "forbidden", "invented") if g[k]} | {k: g[k] for k in ("playbook", "draft") if k in g and not g[k]})))
            lines += [f"### q{i} {mark}: {q['q']}", "", f"Tools: {', '.join(tools) or 'none'}. Grades: "
                      + ", ".join(f"{k} {'ok' if g[k] else 'FAIL'}" for k in ("facts", "numbers", "playbook", "draft") if k in g)
                      + (f". Invented numbers: {g['invented']}" if g["invented"] else "") + (f". Missing: {g['missing']}" if g["missing"] else ""),
                      "", "> " + answer.replace("\n", "\n> "), ""]

    n = len(rows)
    if not n:
        print("no questions run")
        return 1
    mode_ok = sum(r["facts"] for r in rows)
    clean = sum(r["numbers"] for r in rows)
    pb = [r for r in rows if "playbook" in r]
    summary = (f"{sum(r['pass'] for r in rows)}/{n} passed; facts and failure mode {mode_ok}/{n} ({100 * mode_ok / n:.0f}%); "
               f"answers with no invented number {clean}/{n}; playbook {sum(r['playbook'] for r in pb)}/{len(pb)}")
    print(summary)
    out = Path(a.report_dir) / f"eval_assistant_{run}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines[3:3] = [f"**{summary}.** Target: failure mode at least 80%, zero invented numbers.", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"report: {out.as_posix()}")
    return 0 if mode_ok / n >= 0.8 and clean == n else 2


if __name__ == "__main__":
    raise SystemExit(main())
