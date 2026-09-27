# plant-assistant (Part E)

The cognitive maintenance assistant: a Python app on the Claude Agent SDK, powered by a
Claude subscription token (`claude setup-token`), with four tools over the plant API.

| tool | reads | answers |
|---|---|---|
| `get_asset_status(asset_id)` | latest readings (units, baselines), plant load, open alerts, latest risk score, last repair, open work orders | "Status of BFP-2?" |
| `get_events(asset_id, from, to)` | alerts, risk scores and alert days, repairs and work orders, 6-hourly trend per tag | "What happened last week?" |
| `get_recommendations(failure_mode, severity, query)` | the operator playbook (`GET /playbook`, fallback `docs/playbook/`) | "What should we do?" |
| `create_workorder(...)` | nothing: it stores a **draft** | "Create a work order" |

Design rules (system prompt in `assistant/agent.py`): never guess a number, cite tag, value,
unit, time and baseline, recommendations only from the playbook, keep anomaly z-scores and
failure probabilities apart.

**Confirmation flow (E3.3).** The model can only draft. A work order is created by
`POST /workorders/confirm {session_id, draft_id}`, which the dashboard's Confirm button calls;
the model has no tool that writes. Drafts are single use, bound to their session, and expire
after 30 minutes.

**Lockdown.** `tools=[]` (no built-in file, shell or web tools), `setting_sources=[]` (this
repo's CLAUDE.md, hooks and agents never load), an empty working directory, and a
`can_use_tool` callback that refuses anything but the four tools.

## HTTP

- `POST /chat {"message", "session_id"}` streams NDJSON: `text` deltas, `tool` calls, `draft`,
  `done`, or `error`.
- `POST /workorders/confirm {"session_id", "draft_id"}`.
- `GET /health`.
- With `ASSISTANT_SECRET` set (always on the VM), every request needs `X-Demo-Secret`;
  otherwise 401. Limits per viewer (`X-Viewer-IP` from the dashboard Worker): 20 messages and
  10 confirms per hour.

## Run locally

```bash
# .env: CLAUDE_CODE_OAUTH_TOKEN (claude setup-token), PLANT_API_URL, PLANT_READ_TOKEN
uv run plant-assistant                        # 127.0.0.1:8100
curl -N -X POST localhost:8100/chat -H 'content-type: application/json' \
  -d '{"message":"What happened to BFP-2 last week?","session_id":"demo-000001"}'
```

Settings: `ASSISTANT_MODEL` (default `claude-opus-5`), `ASSISTANT_EFFORT`, `ASSISTANT_PORT`,
`ASSISTANT_SECRET`, `ASSISTANT_MESSAGES_PER_HOUR`. On Windows an npm install of Claude Code
puts a `claude.cmd` shim on PATH that the SDK refuses to run; the app finds the native
`claude.exe` inside that npm package, or set `ASSISTANT_CLAUDE_CLI`.

The plant API routes it uses: `/clock`, `/assets`, `/tags/latest`, `/tags/{tag}/history`,
`/maintenance/log`, `/playbook`, `/alerts`, `/predictions` (READ) and `POST /maintenance/workorder`.
`/alerts` and `/predictions` have rows only on the Cloudflare plant API (scoring runs there);
the local FastAPI returns empty lists.

**Hosting:** during demos from your PC through a Cloudflare Quick Tunnel,
`uv run python scripts/run_assistant_tunnel.py` (see `deploy/pc/README.md`). If the plant
database is unavailable (for example the D1 free-tier limit), the tools still return live
readings and mark alerts, risk and work orders as unavailable instead of empty.

Tests: `uv run pytest tests/test_assistant.py` (no Claude call). Evaluation against a running
assistant: `scripts/eval_assistant.py` (golden set in `tests/golden/assistant.yaml`).
