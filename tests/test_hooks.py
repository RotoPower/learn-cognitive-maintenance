"""scripts/block-prod-deploy.sh: production deploys are blocked unless a human approved HEAD."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "block-prod-deploy.sh"
# Full path: on Windows, subprocess would otherwise pick System32's WSL bash.exe before Git Bash.
BASH = shutil.which("bash") or "bash"


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    """A throwaway git repo standing in for the main checkout (approvals live there)."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "safe.directory=*", "commit", "-q", "--allow-empty", "-m", "x"], check=True, env=env)
    sha = subprocess.run(["git", "-C", str(tmp_path), "-c", "safe.directory=*", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    return tmp_path, sha


def run(payload: dict, root: Path) -> tuple[int, str]:
    p = subprocess.run(
        [BASH, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "PROD_GUARD_ROOT": str(root), "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*"},
        timeout=60,
    )
    return p.returncode, p.stderr


def bash(cmd: str, tool: str = "Bash") -> dict:
    return {"tool_name": tool, "tool_input": {"command": cmd}}


BLOCKED = [
    "npx wrangler deploy --env production",
    "cd apps/scoring && npx wrangler deploy --env=production",
    "wrangler deploy",  # top-level config names are the production Worker names
    "wrangler deploy -e production",
    "npx wrangler d1 execute plant-production --env production --remote --command 'DELETE FROM readings'",
    "npx wrangler d1 migrations apply plant-production --remote",
    "wrangler secret put ADMIN_TOKEN --env production",
    "npm run deploy",
    "npm run deploy:production",
    "npm run deploy:staging -- --env production",
    "npx wrangler deploy --env staging && npx wrangler deploy --env production",
    "wrangler rollback --env production",
]
ALLOWED = [
    "npx wrangler deploy --env staging",
    "cd apps/ingest && npm run deploy:staging",
    "npx wrangler d1 execute plant-staging --env staging --remote --command 'SELECT 1'",
    "npx wrangler d1 migrations apply plant-local --local",
    "npx wrangler deploy --dry-run --env production",
    "npx wrangler tail --env production",  # read-only
    "npm test",
    "git status",
    "ls .approvals",
    "cat .approvals/prod-abc1234",
    'git commit -q -F - <<\'EOF\'\nguard: a human approves HEAD with .approvals/prod-<sha>\nEOF',
    "git add .gitignore && git commit -m 'ignore .approvals/'",
]


@pytest.mark.parametrize("cmd", BLOCKED)
def test_blocks_production(repo, cmd: str) -> None:
    code, err = run(bash(cmd), repo[0])
    assert code == 2, cmd
    assert "Production ships from a git tag" in err


@pytest.mark.parametrize("cmd", ALLOWED)
def test_allows_staging_and_reads(repo, cmd: str) -> None:
    code, err = run(bash(cmd), repo[0])
    assert code == 0, (cmd, err)


def test_powershell_tool_is_covered(repo) -> None:
    assert run(bash("npx wrangler deploy --env production", tool="PowerShell"), repo[0])[0] == 2


def test_approval_for_head_unblocks_and_other_shas_do_not(repo) -> None:
    root, sha = repo
    (root / ".approvals").mkdir()
    (root / ".approvals" / "prod-0000000").write_text("")
    assert run(bash("npx wrangler deploy --env production"), root)[0] == 2
    (root / ".approvals" / f"prod-{sha[:7]}").write_text("")
    assert run(bash("npx wrangler deploy --env production"), root)[0] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Write", "tool_input": {"file_path": ".approvals/prod-abc1234", "content": ""}},
        {"tool_name": "Edit", "tool_input": {"file_path": "E:/x/.approvals/prod-abc1234"}},
        bash("mkdir -p .approvals && touch .approvals/prod-$(git rev-parse HEAD)"),
        bash("New-Item .approvals/prod-abc1234", tool="PowerShell"),
        bash("echo > .approvals/prod-abc1234"),
        bash("git rev-parse HEAD | tee .approvals/prod-head"),
        bash("cp /tmp/x .approvals/prod-abc1234"),
        bash("uv run python -c \"open('.approvals/prod-abc1234','w')\""),
        bash("Set-Content -Path .approvals/prod-abc1234 -Value ''", tool="PowerShell"),
    ],
)
def test_claude_cannot_create_approvals(repo, payload: dict) -> None:
    code, err = run(payload, repo[0])
    assert code == 2 and "only a human" in err


def test_other_tools_pass(repo) -> None:
    assert run({"tool_name": "Write", "tool_input": {"file_path": "apps/scoring/README.md"}}, repo[0])[0] == 0
    assert run({"tool_name": "Read", "tool_input": {"file_path": ".approvals/prod-x"}}, repo[0])[0] == 0
