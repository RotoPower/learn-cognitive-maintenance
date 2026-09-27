# Running the assistant from this PC (replaces E3.4's Oracle VM)

The Oracle Always Free VM needs a payment method on file, so the assistant runs on this PC
during demos, reached through a Cloudflare **Quick Tunnel** (`cloudflared tunnel --url`, no
Cloudflare account and no domain needed).

```
viewer's browser -> dashboard Worker /api/chat (adds X-Demo-Secret + viewer IP)
                 -> https://<random>.trycloudflare.com -> cloudflared on this PC
                 -> assistant on 127.0.0.1:8100 -> Claude (your subscription) + plant API (staging)
```

## Why this, and its limits

- **Demo sessions only.** The assistant runs on your Claude subscription through the Agent SDK,
  which counts against your plan limits like interactive Claude Code. Anthropic's guidance for
  shared production automation serving others is an API key on Claude Platform
  (https://support.claude.com/en/articles/15036540). So the chat is on while you present it and
  off otherwise; the rest of the dashboard always works.
- **New address each start.** Quick Tunnels get a random hostname. The script below sets it as
  the dashboard's `ASSISTANT_URL` every time and waits until the dashboard can reach it (a new
  hostname can take a minute to reach every Cloudflare edge).
- **No uptime guarantee** (Cloudflare's testing tunnels). If the PC sleeps or the script stops,
  the chat panel says the assistant is offline.
- **Streaming works** through the tunnel: answers arrive word by word (measured 2026-09-27).

## One-time setup

1. `.env` has `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`), `PLANT_API_URL` (staging) and
   `PLANT_READ_TOKEN`.
2. `cloudflared` on PATH (`winget install Cloudflare.cloudflared`) and a logged-in `wrangler`.
3. Create the shared secret, in `.env` and on the dashboard Worker, never printed:

   ```bash
   uv run python scripts/run_assistant_tunnel.py --init-secret
   ```

## Every demo

```bash
uv run python scripts/run_assistant_tunnel.py
```

It starts the assistant, starts the tunnel, sets `ASSISTANT_URL` on the staging dashboard, and
prints `READY` once the dashboard reaches the assistant. Keep the terminal open and the PC awake
(Settings -> Power -> Sleep: never, while presenting). Ctrl+C stops the assistant and the tunnel,
whole process trees included.

Check without spending a Claude turn: the Confirm route with a made-up draft id reaches the
assistant and gets its 404.

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://plant-dashboard-staging.rotopower.workers.dev/api/chat/confirm \
  -H "content-type: application/json" -d '{"session_id":"probe-00000001","draft_id":"nope"}'   # 404 = reachable, 503 = offline
```

## Moving off this PC later

The script is the only PC-specific part. A named Cloudflare Tunnel on your own domain gives a
fixed address (set `ASSISTANT_URL` once); an always-on host needs the assistant as a service
(`uv run plant-assistant` with `ASSISTANT_SECRET`, `ASSISTANT_HOST=127.0.0.1`). For an always-on
chat that serves others, switch the SDK to an API key (`ANTHROPIC_API_KEY`; the app currently
blanks it on purpose so the subscription is used).
