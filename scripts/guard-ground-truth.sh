#!/usr/bin/env bash
# PreToolUse hook for the data and modeler agents (installed in their frontmatter):
# ground truth is never a model input and only the validator reads it (CLAUDE.md).
# That includes the scenario scripts plant/faults*.yaml, which spell out every onset
# and failure: the data agent once cited a faults.yaml onset in a data report.
# Blocks reading them (Read/Grep/Glob), shell access to them, and the admin
# ground-truth route. The validator-approved label script is run by the orchestrator,
# not by these agents. Exit 2 = block; stderr goes back to the agent.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Same interpreter resolution as the other hooks: never fall back to `uv run`, which
# would make the hook fail open in a worktree without a venv.
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

tool = payload.get("tool_name", "")
inp = payload.get("tool_input") or {}

GT = re.compile(
    r"ground[_-]?truth"                      # ground_truth.json, /admin/ground_truth, plantctl ground-truth
    r"|faults[\w.-]*\.ya?ml"                 # plant/faults.yaml, faults_history_2023.yaml, ...
    r"|\bplant\.sim\b.*\bfailures\b|\.failures\(\)",  # the simulator's failure table
    re.I,
)


def block(what: str) -> None:
    sys.stderr.write(
        f"BLOCKED by scripts/guard-ground-truth.sh: {what}.\n"
        "Ground truth and the fault scenario scripts are validator-only (CLAUDE.md). Work from sensors, "
        "the CMMS maintenance log and data/derived tables; describe what the data shows, not the script.\n"
    )
    sys.exit(2)


if tool in ("Read", "Grep", "Glob", "NotebookRead"):
    fields = " ".join(str(inp.get(k, "")) for k in ("file_path", "path", "pattern", "glob", "notebook_path"))
    if GT.search(fields):
        block(f"{tool} of {fields.strip()}")
    if tool == "Grep":
        # Content search over a folder that holds scenario scripts or ground truth reads them
        # without naming them. Allow it only when a glob/type excludes YAML and JSON.
        path = str(inp.get("path", "")).replace("\\", "/").rstrip("/")
        broad = path in ("", ".") or re.search(r"(^|/)(plant|data|sim|learn-cognitive-maintenance)$", path) is not None
        narrow = str(inp.get("glob", "")) + " " + str(inp.get("type", ""))
        if broad and (not narrow.strip() or re.search(r"ya?ml|json|\*\s*$|^\s*\*\*?\s*$", narrow, re.I)):
            block(f"Grep over '{path or '.'}' would read plant/faults*.yaml or ground_truth.json; narrow it with a glob or type such as '*.py'")
elif tool in ("Bash", "PowerShell"):
    cmd = str(inp.get("command", ""))
    if GT.search(cmd):
        block(f"command touches ground truth: {cmd[:200]}")
sys.exit(0)
PYEOF
