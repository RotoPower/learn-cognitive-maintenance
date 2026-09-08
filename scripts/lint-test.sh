#!/usr/bin/env bash
# PostToolUse hook for Edit|Write: byte-compile the touched Python file and run
# the test suite. Exit 2 (stderr to Claude) when something fails, so the failure
# is seen immediately instead of at the end of the task. Non-Python files: no-op.
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
path="$($PY -c 'import json,sys
try: print((json.load(sys.stdin).get("tool_input") or {}).get("file_path",""))
except Exception: print("")')"
case "$path" in
  *.py) ;;
  *) exit 0 ;;
esac
cd "$ROOT" || exit 0

if ! out="$(uv run python -m py_compile "$path" 2>&1)"; then
  printf 'lint-test: %s does not compile:\n%s\n' "$path" "$out" >&2; exit 2
fi
out="$(uv run pytest -q -x --no-header -p no:cacheprovider 2>&1)"; rc=$?
if [ $rc -ne 0 ]; then
  printf 'lint-test: pytest failed after editing %s\n%s\n' "$path" "$(printf '%s\n' "$out" | tail -n 25)" >&2; exit 2
fi
printf 'lint-test: %s\n' "$(printf '%s\n' "$out" | tail -n 1)"
exit 0
