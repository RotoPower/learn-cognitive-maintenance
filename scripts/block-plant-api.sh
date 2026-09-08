#!/usr/bin/env bash
# PreToolUse hook scoped to the modeler agent (see .claude/agents/modeler.md):
# the modeler must never query the plant API or deploy. It works from
# data/derived tables prepared by the data agent and scores via the model CLIs.
# Exit 2 = block; stderr goes back to the agent.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if   [ -x "$ROOT/.venv/Scripts/python.exe" ]; then PY="$ROOT/.venv/Scripts/python.exe"
elif [ -x "$ROOT/.venv/bin/python" ];        then PY="$ROOT/.venv/bin/python"
else PY="uv run --project $ROOT python"; fi
HOOK_PAYLOAD="$(cat)"; export HOOK_PAYLOAD
exec $PY - <<'PYEOF'
import json, os, re, sys

try:
    payload = json.loads(os.environ.get("HOOK_PAYLOAD", ""))
except Exception:
    sys.exit(0)

_dbg = os.path.join(os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp", "plant-hook-debug.jsonl")
try:  # TEMP diagnostic (remove once agent_type gating is confirmed)
    with open(_dbg, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({k: (v if k != "tool_input" else {"command": str(v.get("command", ""))[:80]}) for k, v in payload.items()}) + "\n")
except Exception:
    pass

if payload.get("tool_name") != "Bash":
    sys.exit(0)
# Installed both in the modeler's frontmatter and project-wide in .claude/settings.json.
# Project-wide, only act when the caller is the modeler subagent (hook payload carries agent_type).
agent = payload.get("agent_type")
if agent is not None and agent != "modeler":
    sys.exit(0)
if agent is None and os.environ.get("BLOCK_PLANT_API_SCOPE", "modeler") == "modeler":
    sys.exit(0)  # main session (no agent_type): not the modeler, allow
cmd = str((payload.get("tool_input") or {}).get("command", ""))

API_CLIENTS = re.compile(
    r"\bplantctl\b|scripts[/\\]plantctl\.py|plant\.cli\b"                 # our CLI, any spelling
    r"|\b(curl|wget|http|https|httpie|Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b"
    r"|urllib\.request|requests\.(get|post|put|delete|request)|httpx\.",  # python http clients
    re.I,
)
API_TARGETS = re.compile(
    r"127\.0\.0\.1:\d+|localhost:\d+|PLANT_API_URL|PLANT_(READ|ADMIN)_TOKEN"
    r"|/admin/|/tags/|/maintenance/|/clock\b|/assets\b",
    re.I,
)
DEPLOY = re.compile(r"\bwrangler\s+(deploy|publish|d1\s+execute|secret)\b", re.I)

reason = None
if re.search(r"\bplantctl\b|scripts[/\\]plantctl\.py|plant\.cli\b", cmd, re.I):
    reason = "plantctl is the plant API client"
elif API_CLIENTS.search(cmd) and API_TARGETS.search(cmd):
    reason = "HTTP call to the plant API"
elif re.search(r"\buvicorn\b.*plant\.api|plant\.api:app", cmd):
    reason = "starting the plant API"
elif DEPLOY.search(cmd):
    reason = "deploying is the deployer's job"

if reason:
    sys.stderr.write(
        f"BLOCKED by scripts/block-plant-api.sh: {reason}.\n"
        "The modeler works only from data/derived tables prepared by the data agent and scores with the "
        "model CLIs (`uv run python -m models.anomaly ...`, `uv run python -m models.predict ...`). "
        "Ask the orchestrator to have the data agent fetch what you need.\n"
    )
    sys.exit(2)
sys.exit(0)
PYEOF
