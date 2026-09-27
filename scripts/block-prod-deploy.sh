#!/usr/bin/env bash
# PreToolUse hook (project-wide, every agent and the main session): production is
# deployed by CI from a git tag, never from here. Blocks any Cloudflare-changing
# command that does not target staging, unless a human has approved this exact commit
# with `.approvals/prod-<sha>` (full sha or >= 7-char prefix of HEAD). Also blocks
# Claude from creating or editing approvals itself.
# Exit 2 = block; stderr goes back to the agent.
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
# Approvals live in the main checkout, so a worktree agent cannot bring its own.
export PROD_GUARD_ROOT="${PROD_GUARD_ROOT:-${MAIN:-$ROOT}}"
exec $PY - <<'PYEOF'
import json, os, re, subprocess, sys
from pathlib import Path

try:
    payload = json.loads(os.environ.get("HOOK_PAYLOAD", ""))
except Exception:
    sys.exit(0)

tool = payload.get("tool_name")
inp = payload.get("tool_input") or {}
root = Path(os.environ["PROD_GUARD_ROOT"])


def block(msg: str) -> None:
    sys.stderr.write(f"BLOCKED by scripts/block-prod-deploy.sh: {msg}\n")
    sys.exit(2)


APPROVALS = re.compile(r"(^|[/\\\s\"'=])\.approvals([/\\]|\s|$|[\"'])")

# 1. approvals are written by a human only
if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
    if APPROVALS.search(str(inp.get("file_path", "") or inp.get("notebook_path", ""))):
        block("only a human creates production approvals (.approvals/prod-<sha>).")
    sys.exit(0)
if tool not in ("Bash", "PowerShell"):
    sys.exit(0)
cmd = str(inp.get("command", ""))
# A writer (redirect, file tool, cmdlet, python write) followed by .approvals on the same
# line. Mentions alone pass: commit messages and docs talk about approvals.
APPROVAL_WRITE = re.compile(
    r"(>|\btee\b|\btouch\b|\bmkdir\b|\bcp\b|\bmv\b|\brm\b|\bln\b|\binstall\b|\bdd\b|\bsed\s+-i"
    r"|New-Item|Set-Content|Add-Content|Out-File|Copy-Item|Move-Item|Remove-Item|Rename-Item|ni\b"
    r"|open\(|write_text|write_bytes|\.write\(|os\.(makedirs|mkdir|rename|replace)|shutil\.)"
    r"[^\n]*?\.approvals",
    re.I,
)
if APPROVALS.search(cmd) and APPROVAL_WRITE.search(cmd):
    block("only a human creates or changes production approvals (.approvals/prod-<sha>); reading or mentioning them is fine.")

# 2. production targets
MUTATING = re.compile(
    r"\bwrangler(\.cmd)?\s+(deploy|publish|delete|rollback|versions\s+(deploy|upload)|secret\s+(put|delete|bulk)"
    r"|d1\s+(execute|migrations\s+apply|import|delete)|kv\s+\S+\s+(put|delete)|r2\s+\S+\s+(put|delete))\b",
    re.I,
)
ENV_FLAG = re.compile(r"(?:--env|-e)(?:=|\s+)[\"']?([A-Za-z0-9_-]+)")
NPM_DEPLOY = re.compile(r"\bnpm\s+run\s+(deploy[\w:-]*)|\bnpx\s+wrangler\b", re.I)

reasons = []
for seg in re.split(r"&&|\|\||;|\|", cmd):
    if re.search(r"--dry-run\b", seg):
        continue  # bundles only, changes nothing
    if MUTATING.search(seg):
        envs = ENV_FLAG.findall(seg)
        local = re.search(r"--local\b", seg) and re.search(r"\bd1\b", seg)
        if local:
            continue
        if not envs:
            reasons.append("wrangler without --env targets the top-level config, whose Worker names are the production ones")
        elif any(e != "staging" for e in envs):
            reasons.append(f"wrangler --env {','.join(e for e in envs if e != 'staging')}")
    m = NPM_DEPLOY.search(seg)
    if m and m.group(1) and m.group(1) != "deploy:staging":
        reasons.append(f"npm run {m.group(1)}")
    elif m and m.group(1) and any(e != "staging" for e in ENV_FLAG.findall(seg)):
        reasons.append("npm run deploy:staging with a non-staging --env")
if re.search(r"\bdeploy[:-]?prod(uction)?\b", cmd, re.I) and not reasons:
    reasons.append("production deploy script")

if not reasons:
    sys.exit(0)

try:
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
except Exception:
    head = ""
approved = False
if re.fullmatch(r"[0-9a-f]{40}", head):
    for f in (root / ".approvals").glob("prod-*"):
        sha = f.name[len("prod-"):]
        if len(sha) >= 7 and re.fullmatch(r"[0-9a-f]+", sha) and head.startswith(sha):
            approved = True
if approved:
    sys.exit(0)

block(
    "; ".join(dict.fromkeys(reasons)) + ". Only staging is deployed from here (`--env staging`, `npm run deploy:staging`). "
    f"Production ships from a git tag through CI. A human can approve this commit ({head[:12] or 'unknown HEAD'}) "
    "with `.approvals/prod-<sha>`."
)
PYEOF
