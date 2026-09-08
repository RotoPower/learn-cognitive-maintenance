#!/usr/bin/env bash
# PreToolUse hook: block any tool call that would write to data/raw/.
# Reads the hook payload JSON on stdin. Exit 2 = block (stderr goes back to Claude).
# Reading data/raw is allowed; only mutations are stopped. CLAUDE.md: data/raw is read-only.
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
HOOK_PAYLOAD="$(cat)"; export HOOK_PAYLOAD   # stdin is reused for the script below
exec $PY - <<'PYEOF'
import json, os, re, sys

try:
    payload = json.loads(os.environ.get("HOOK_PAYLOAD", ""))
except Exception:
    sys.exit(0)  # unparseable payload: do not block

tool = payload.get("tool_name", "")
inp = payload.get("tool_input", {}) or {}
SEP = r"[/\\]"  # forward or back slash
RAW = re.compile(r"data" + SEP + r"+raw(?=" + SEP + r"|$|[^A-Za-z0-9_])", re.I)


def block(reason: str) -> None:
    sys.stderr.write(
        f"BLOCKED by scripts/guard-raw-data.sh: {reason}\n"
        "data/raw/ is read-only (CLAUDE.md). Write to data/derived/ or data/sim/ instead.\n"
    )
    sys.exit(2)


if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
    path = str(inp.get("file_path", "") or inp.get("notebook_path", ""))
    if RAW.search(path):
        block(f"{tool} targets {path}")

elif tool == "Bash":
    cmd = str(inp.get("command", ""))
    if RAW.search(cmd):
        shell_writers = re.compile(
            r"(^|[;&|(]|\s)(rm|mv|cp|mkdir|touch|tee|truncate|dd|install|rsync|unzip|tar|curl|wget|chmod|chown|ln|rmdir|shred)(\s|$)"
            r"|sed\s+-[a-zA-Z]*i"
            r"|>{1,2}\s*[^&]"
        )
        if shell_writers.search(cmd):
            block(f"shell command mutates data/raw: {cmd}")
        py_writers = re.compile(
            r"to_(csv|parquet|pickle|json|feather|excel)|write_(csv|parquet|text|bytes)|\.write\(|"
            r"open\([^)]*['\"][wax]|savefig|\.save\(|mkdir|unlink|remove\(|rename\(|shutil\.",
            re.I,
        )
        if re.search(r"python|uv\s+run|\bpy\s", cmd, re.I) and py_writers.search(cmd):
            block(f"python command writes near data/raw: {cmd}")
        if re.search(r"Set-Content|Add-Content|Out-File|Remove-Item|New-Item|Move-Item|Copy-Item", cmd, re.I):
            block(f"PowerShell command mutates data/raw: {cmd}")

sys.exit(0)
PYEOF
