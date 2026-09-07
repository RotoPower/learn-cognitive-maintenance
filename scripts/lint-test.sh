#!/usr/bin/env bash
# PostToolUse hook for Edit|Write: byte-compile the touched Python file and run
# the test suite. Exit 2 (stderr to Claude) when something fails, so the failure
# is seen immediately instead of at the end of the task. Non-Python files: no-op.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if   [ -x "$ROOT/.venv/Scripts/python.exe" ]; then PY="$ROOT/.venv/Scripts/python.exe"
elif [ -x "$ROOT/.venv/bin/python" ];        then PY="$ROOT/.venv/bin/python"
else PY="uv run --project $ROOT python"; fi
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
