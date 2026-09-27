"""Run the Part E assistant on this PC and expose it to the staging dashboard (replaces E3.4's VM).

    uv run python scripts/run_assistant_tunnel.py --init-secret   # once: create ASSISTANT_SECRET
    uv run python scripts/run_assistant_tunnel.py                 # every demo session; Ctrl+C to stop

What one run does:
  1. starts the assistant (`uv run plant-assistant`) on 127.0.0.1:8100 with ASSISTANT_SECRET;
  2. starts a Cloudflare Quick Tunnel (`cloudflared tunnel --url ...`, no account needed) and
     reads its random https://<name>.trycloudflare.com address;
  3. sets that address as the staging dashboard Worker's ASSISTANT_URL secret (wrangler), so
     the chat panel reaches this PC;
  4. keeps running until Ctrl+C, then stops the assistant and the tunnel (whole process trees).
While it is not running, the dashboard works and the chat panel says the assistant is offline.

--init-secret generates a random ASSISTANT_SECRET, appends it to .env and sets the same value
on the dashboard Worker; it is never printed. The assistant rejects requests without it.

Quick Tunnels are Cloudflare's free testing tunnels: a new address each start, no uptime
guarantee. Fine for live demos from this PC; see deploy/pc/README.md.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "apps" / "dashboard"
ENV_FILE = ROOT / ".env"
PORT = int(os.environ.get("ASSISTANT_PORT", 8100))
TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
UA = "run-assistant-tunnel/0.1"


def say(msg: str) -> None:
    print(f"[assistant-tunnel] {msg}", flush=True)


def load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)


def wrangler_secret(name: str, value: str) -> None:
    """`wrangler secret put <name> --env staging` for the dashboard, value on stdin (never argv)."""
    # On Windows, which("npx") can return npm's extensionless shell shim (WinError 193): use npx.cmd.
    npx = (shutil.which("npx.cmd") if os.name == "nt" else None) or shutil.which("npx")
    if not npx:
        raise SystemExit("npx not found: install Node.js")
    r = subprocess.run([npx, "wrangler", "secret", "put", name, "--env", "staging"], cwd=DASHBOARD,
                       input=value + "\n", text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=180)
    if r.returncode != 0:
        raise SystemExit(f"wrangler secret put {name} failed:\n{(r.stderr or r.stdout)[-800:]}")
    say(f"dashboard (staging) secret {name} set")


def init_secret() -> None:
    load_env()
    if os.environ.get("ASSISTANT_SECRET"):
        say("ASSISTANT_SECRET already in .env; setting the same value on the dashboard Worker")
        value = os.environ["ASSISTANT_SECRET"]
    else:
        value = secrets.token_urlsafe(32)
        with ENV_FILE.open("a", encoding="utf-8") as f:
            f.write(f"\nASSISTANT_SECRET={value}\n")
        say("generated ASSISTANT_SECRET and appended it to .env (not printed)")
    wrangler_secret("ASSISTANT_SECRET", value)


def kill_tree(p: subprocess.Popen | None) -> None:
    """Stop a child and everything it started (uv -> python -> claude.exe; cloudflared)."""
    if p is None or p.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
    else:
        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()


def wait_http(url: str, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"user-agent": UA}), timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def dashboard_reaches_assistant(timeout: float) -> bool:
    url = os.environ.get("DASHBOARD_URL", "https://plant-dashboard-staging.rotopower.workers.dev").rstrip("/")
    body = b'{"session_id": "tunnel-probe-0001", "draft_id": "probe-only"}'
    end = time.time() + timeout
    while time.time() < end:
        req = urllib.request.Request(f"{url}/api/chat/confirm", data=body, method="POST",
                                     headers={"content-type": "application/json", "user-agent": UA})
        try:
            urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError as e:
            if e.code == 404:  # the assistant answered: no such draft
                return True
        except Exception:
            pass
        time.sleep(5)
    return False


def spawn(cmd: list[str], env: dict | None = None) -> subprocess.Popen:
    kw: dict = {"cwd": ROOT, "env": env, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True,
                "encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(cmd, **kw)


def run() -> int:
    load_env()
    for need in ("CLAUDE_CODE_OAUTH_TOKEN", "ASSISTANT_SECRET", "PLANT_API_URL", "PLANT_READ_TOKEN"):
        if not os.environ.get(need):
            hint = " (run with --init-secret once)" if need == "ASSISTANT_SECRET" else ""
            raise SystemExit(f"{need} is not set in .env{hint}")
    cloudflared = shutil.which("cloudflared")
    uv = shutil.which("uv")
    if not cloudflared or not uv:
        raise SystemExit("needs `cloudflared` and `uv` on PATH (winget install Cloudflare.cloudflared)")

    assistant = tunnel = None
    try:
        env = {**os.environ, "ASSISTANT_PORT": str(PORT), "ASSISTANT_HOST": "127.0.0.1", "PYTHONUNBUFFERED": "1"}
        assistant = spawn([uv, "run", "plant-assistant"], env)
        threading.Thread(target=lambda: [print(f"  assistant | {line}", end="", flush=True) for line in assistant.stdout], daemon=True).start()
        if not wait_http(f"http://127.0.0.1:{PORT}/health", 60):
            raise SystemExit("the assistant did not come up on port %d" % PORT)
        say(f"assistant up on 127.0.0.1:{PORT} (plant API {os.environ['PLANT_API_URL']})")

        tunnel = spawn([cloudflared, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"])
        url, found = None, threading.Event()

        def read_tunnel() -> None:
            nonlocal url
            for line in tunnel.stdout:
                m = TUNNEL_RE.search(line)
                if m and not url:
                    url = m.group(0)
                    found.set()
                if re.search(r"\b(ERR|error)\b", line) and "Retrying" not in line:
                    print(f"  cloudflared | {line}", end="", flush=True)

        threading.Thread(target=read_tunnel, daemon=True).start()
        if not found.wait(60):
            raise SystemExit("cloudflared did not report a trycloudflare.com address within 60 s")
        say(f"tunnel {url}")
        if not wait_http(f"{url}/health", 90):
            raise SystemExit("the tunnel address does not answer yet; try again")
        wrangler_secret("ASSISTANT_URL", url)
        # A fresh trycloudflare hostname can take a minute to reach every Cloudflare edge: until
        # then the Worker gets 530. Probe through the dashboard with a bogus draft: the assistant
        # answers 404 without calling Claude.
        if dashboard_reaches_assistant(90 * 2):
            say("READY: the staging dashboard's chat panel now reaches this PC. Ctrl+C to stop.")
        else:
            say("the dashboard cannot reach the tunnel yet; it usually does within a few minutes. Ctrl+C to stop.")
        while assistant.poll() is None and tunnel.poll() is None:
            time.sleep(1)
        say("a child process exited; shutting down")
        return 1
    except KeyboardInterrupt:
        say("stopping")
        return 0
    finally:
        kill_tree(tunnel)
        kill_tree(assistant)
        say("assistant and tunnel stopped; the chat panel will say the assistant is offline")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--init-secret", action="store_true", help="create ASSISTANT_SECRET in .env and on the dashboard Worker")
    a = p.parse_args()
    if a.init_secret:
        init_secret()
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
