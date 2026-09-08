#!/usr/bin/env bash
# PreToolUse hook scoped to the modeler agent (see .claude/agents/modeler.md):
# the modeler must never query the plant API or deploy. It works from
# data/derived tables prepared by the data agent and scores via the model CLIs.
# Exit 2 = block; stderr goes back to the agent.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# In an agent worktree (.claude/worktrees/<agent>) there is no .venv; use the main
# checkout's venv (via git's common dir), then any system python. Never fall back to
# `uv run`, which would have to build a venv and makes the hook fail open.
MAIN="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; MAIN="${MAIN%/.git}"
PY=""
for c in "$ROOT/.venv/Scripts/python.exe" "$ROOT/.venv/bin/python" "$MAIN/.venv/Scripts/python.exe" "$MAIN/.venv/bin/python"; do
  [ -n "$c" ] && [ -x "$c" ] && { PY="$c"; break; }
done
if [ -z "$PY" ]; then
  for c in python python3 py; do
    if "$c" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi
if [ -z "$PY" ]; then echo "hook: no python interpreter found; refusing to fail open" >&2; exit 2; fi
HOOK_PAYLOAD="$(cat)"; export HOOK_PAYLOAD
exec $PY - <<'PYEOF'
import json, os, re, sys

try:
    payload = json.loads(os.environ.get("HOOK_PAYLOAD", ""))
except Exception:
    sys.exit(0)

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
