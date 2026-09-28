# How this project uses Claude Code

Two kinds of agents live here. **Dev-time** agents help build the models and the platform
(skills, subagents, hooks, all under `.claude/`). The **run-time** agent is the assistant
inside the product (`apps/assistant`, Part E). Rules for both come from `CLAUDE.md`: `data/raw`
is read-only, ground truth is never a model input and only the validator reads it, every run
has a seed and a run id.

## Dev-time team

| piece | kind | what it does | what it must not be able to do |
|---|---|---|---|
| `/eda` | skill | profile a dataset, write a findings report | - |
| `maintenance-domain` | skill (preloaded) | tags, units, baselines, failure modes and their symptoms | - |
| `/anomaly`, `/predict` | skills | the method for each model task | - |
| `/pipeline <task> <target> <horizon>` | skill, user-invoked only | frame -> data -> model -> validate -> ask -> deploy staging | run a step out of order or skip the validator |
| `data` | subagent | profiles and prepares `data/derived` tables from sensors and the CMMS log | read ground truth or `plant/faults*.yaml`; write `data/raw` |
| `modeler` | subagent, worktree | writes model code, prepares Colab runs, scores via the CLIs | call the plant API, deploy, read ground truth |
| `validator` | subagent | adversarial GO / NO-GO: leakage, time split, metrics recomputed against ground truth | edit files |
| `deployer` | subagent | staging deploys, artefact upload, one scoring pass, health, logs | deploy production |

Why skills vs subagents: a **skill** is a method loaded into whoever runs it (the task's
recipe); a **subagent** is a separate context with its own tools, permissions and hooks, so
it can be walled off (the modeler cannot reach the API; the validator cannot write).

### Hooks: controls, not requests

A prompt line is a request; a hook is enforced for every tool call. The modeler was told not to
query the plant API and did it anyway, which is why these exist (`scripts/`):

| hook | where | blocks |
|---|---|---|
| `guard-raw-data.sh` | project, Bash/PowerShell/Write/Edit | any write to `data/raw` |
| `block-prod-deploy.sh` | project, Bash/PowerShell/Write/Edit; deployer | Cloudflare-changing commands not aimed at staging, unless a human wrote `.approvals/prod-<sha>` for HEAD; Claude writing approvals |
| `block-plant-api.sh` | modeler | plant API calls, starting the API, deploys |
| `guard-ground-truth.sh` | data, modeler | ground truth, `plant/faults*.yaml`, the admin ground-truth route, repo-wide content searches that would read them |
| `lint-test.sh` | project, after Edit/Write of `.py` | runs pytest; a failure goes back to Claude |

Tests for the hooks: `tests/test_hooks.py`. All hooks resolve Python from the main checkout
and refuse to fail open.

### MCP, memory, CI

- `.mcp.json` (project scope): `claude-code-docs` (Claude Code documentation). Each user approves it once.
  The Cloudflare MCP server is attached only to the `deployer` agent.
- Agent memory: `.claude/agent-memory/data/` (dataset facts the data agent keeps between runs).
  Your own Claude memory (outside the repo) is not shared through git.
- CI/CD (`docs/ci-cd.md`): Workers Builds deploys staging on every push to `main`; production
  only from a `v*` tag after a required reviewer approves in GitHub.

### What a fresh clone gets

Skills, subagents, hooks, `.mcp.json`, the golden set, tests and all code. Not: tokens (`.env`,
`.dev.vars`, Worker secrets, the Claude subscription token), `data/` (regenerate with
`uv run python -m plant.sim` and the prep scripts), `.approvals/`, anyone's personal memory.

## Run-time assistant (Part E)

`apps/assistant`: Claude Agent SDK, four tools over the plant API, never a built-in tool
(details in its README). It runs on your PC during demos, behind a Cloudflare Quick Tunnel
(`scripts/run_assistant_tunnel.py`, `deploy/pc/README.md`): the Oracle VM of the module needs a
payment method, and a subscription-powered chat belongs in sessions you present, not always on. Numbers must come from tools because the operator acts on them: a
guessed vibration value or date is worse than "I don't have that". `scripts/eval_assistant.py`
checks it against `tests/golden/assistant.yaml` (failure mode named, no invented number,
playbook actions); run it after every prompt change.

## The 10-minute demo

Staging URLs: dashboard https://plant-dashboard-staging.rotopower.workers.dev (public, no login).

Before: `uv run plantctl --admin reset` (clock to the scripted start), then
`uv run plantctl --admin jump --to 2024-08-18T00:00:00` (day 230) and let ingest catch up
for a minute. BFP2's scripted bearing wear starts at day 240; it fails on day 270 (2024-09-27).

1. **Phone, dashboard.** Fleet healthy at day 230, KPIs green.
2. **Demo controls -> Jump clock +7 days, four times, waiting about a minute between jumps.**
   The ingest cron backfills at most 7 days per minute; jumping further at once would leave a
   permanent gap in the readings and the anomaly baseline (30 days) would not score. Then
   **Run scoring now**. Around day 258 (2024-09-14) BFP2 turns red: the risk model alerts after
   two consecutive days above threshold (validated: first alert 2024-09-13, 14 days ahead).
   One more +7 and scoring shows the DE vibration anomaly alert (from 2024-09-16).
3. **Chat panel** (start `uv run python scripts/run_assistant_tunnel.py` before the demo and wait
   for READY): "What happened to BFP-2 this week?" then "What should we do?". It cites
   `BFP2.VIB_DE` and `BRG_TEMP_DE` with values, times and baselines, and the bearing-wear
   playbook actions.
4. **Asset drawer -> Create work order**, review, **Confirm** (or ask the chat to draft one and
   press Confirm on the card).
5. **Reveal ground truth**: `uv run plantctl --admin ground-truth` (validator-only in the dev
   team): the failure was scripted for day 270, so the warning came about two weeks early.
6. **Claude Code**: show `/pipeline anomaly fleet 7d` walking steps 2-3 with the subagents, and the
   hooks refusing a production deploy and a ground-truth read.

If staging's D1 is over its free daily limit, every data route says so (503 with the reason);
it resets at 00:00 UTC (07:00 WIB).
