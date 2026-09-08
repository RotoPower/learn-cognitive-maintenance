# Claude Code for Data Science — Learning Module
### Project: cognitive maintenance for a fictional power plant, built with Claude Code, deployed free

You learn Claude Code by building one real project from scratch. Every lesson adds a piece; by the end you have a working demo that you can show and explain.

**Repository**: `learn-cognitive-maintenance` — Python package name inside: `plant`.

**What you'll have at the end**
- A fake power plant that streams sensor data through an API with a simulated clock
- A team of Claude Code subagents (data, modeler, validator, deployer) driven by one `/run` command
- Two maintenance models (anomaly, failure prediction) trained in Colab, scored nightly on Cloudflare
- A dashboard and an operator chatbot ("what happened to BFP-2? what should we do?") on Claude
- All of it on free tiers: Cloudflare Free, Google Colab, Oracle Always Free, your Claude Max plan

**Time**: about 5 weeks at 8–10 hours a week. Each phase ends with a checkpoint; don't start the next phase until the checkpoint passes.

---

## How to use this module

1. Read **Part A** once (30 min). It's the setup and the five ideas everything else depends on.
2. Work through **Parts B–E** in order. Each has: *Goal → Build → Learn from → Checkpoint*. "Learn from" links are what to read *before* the step, not after.
3. Everything marked **Later** is not part of the MVP. Skip it the first time through; **Part F** collects those upgrades.
4. Use Claude Code for every step, including writing the files this module describes. Ask it in plan mode first (`Shift+Tab`), read the plan, then approve.

Companion: `claude-code-learning-module.md` (general version, same concepts without the plant). This one is self-contained.

---

## The plan at a glance

| Week | Part | You build | You learn | Checkpoint |
|---|---|---|---|---|
| 1 | B | fake plant + API with sim clock; `/eda`; `data` subagent; first hook | CLAUDE.md, skills, subagents, hooks | BFP-2 vibration rises after day 240; `data` can't touch raw data |
| 2–3 | C | `modeler`, `validator`, `/run`; anomaly + predict models (Colab) | orchestration, skills vs subagents, leakage discipline | `/run anomaly` flags BFP-2 with measured lead time |
| 4 | D | plant on Cloudflare (Worker, DO, D1, cron); public management dashboard; `deployer` | MCP, environments, guardrails on deploys | dashboard works from a phone; prod deploy is blocked by hook |
| 5 | E | assistant on Oracle VM (Agent SDK, Max plan), embedded in the dashboard | run-time agents vs dev-time agents, tool-grounded answers | chat explains the BFP-2 alert and creates a confirmed work order |
| — | F | 10-minute demo; self-assessment | — | you can answer F2 without notes |

---

# PART A — Setup and the five ideas (30 min)

## A1. Windows setup (native, Git already installed)

Node, Git for Windows, and Claude Code are installed. Do these once, in PowerShell:

```powershell
claude doctor                                   # confirm the install is healthy
git config --global core.autocrlf input         # keep files LF; CRLF breaks SKILL.md frontmatter
winget install astral-sh.uv                     # Python + venv manager used throughout
npm install -g wrangler                         # Cloudflare CLI (Part D)
```

Pin Git Bash so Claude Code's Bash tool never picks up a stray WSL `bash.exe`. In `%USERPROFILE%\.claude\settings.json`:

```json
{ "env": { "CLAUDE_CODE_GIT_BASH_PATH": "C:\\Program Files\\Git\\bin\\bash.exe" } }
```

Conventions used in every file in this module so they work on Windows:
- Forward-slash paths everywhere (`data/raw`). Git Bash and Python accept them.
- Hook commands are `bash scripts/x.sh` (never `./x.sh`; Windows ignores executable bits).
- CLIs are Python entry points (`uv run plantctl ...`), not shell scripts.
- No `tmux`: Windows Terminal split panes (`Alt+Shift+D`), one `claude` per pane.
- No cron locally: Task Scheduler if you need it; on Cloudflare it's Cron Triggers anyway.
- No Docker needed for the MVP.

Docs: https://code.claude.com/docs/en/setup

## A2. The five ideas

| Piece | What it is | In this project |
|---|---|---|
| **CLAUDE.md** | Facts Claude always knows about the repo | "raw data is read-only; use `uv run`; tag names look like `GT1.EXH_TEMP`" |
| **Skill** | A reusable procedure, loaded on demand. Lives in `.claude/skills/<name>/SKILL.md`, invoked as `/name` | `/eda`, `/anomaly`, `/predict`, `/run` |
| **Subagent** | A separate worker with its own context window and its own tool allowlist. Lives in `.claude/agents/<name>.md` | `data`, `modeler`, `validator`, `deployer` |
| **MCP server** | A connection to an outside system exposed as tools | Cloudflare (for the deployer), Claude Code docs |
| **Hook** | A shell command that runs automatically on an event, no LLM involved | block writes to `data/raw`; block `wrangler deploy --env production` |

One sentence to memorise: **CLAUDE.md = what Claude always knows · Skills = what Claude can do on demand · MCP = what Claude can reach · Subagents = where Claude thinks in isolation · Hooks = what must happen no matter what.**

Decision rule for any new task: *same procedure every time?* → skill. *Long or noisy output?* → subagent. *Needs something outside the repo?* → MCP. *Must never happen?* → hook. *True every session?* → CLAUDE.md.

## A3. Reading your two diagrams

- Your **mind map** is the syllabus; this module follows it (Sesi 1 = Part B, Sesi 2 = Parts C–E).
- Your **architecture diagram** has two ideas: the tmux panes at the top are *separate sessions* that share nothing (on Windows: Terminal split panes); the orchestrator + subagents at the bottom live *inside one session*. You build the bottom row. `/loop` is a bundled Claude Code skill; `/goals` was someone's custom skill — yours is called `/run`.

## A4. MVP scope — what's in, what's later

| In the MVP | Later (Part F) |
|---|---|
| 4 assets (GT-1, BFP-1, BFP-2, CTF-1), ~12 tags, 3 failure modes, 1 year hourly | 14 assets, 8 failure modes, 2 years |
| Models: **anomaly** (z-score, no training) + **predict** (logistic regression, Colab) | RUL, forecast, C-MAPSS benchmark, ONNX models |
| Subagents: `data`, `modeler`, `validator`, `deployer` | `reporter`, `assistant-evaluator`, coordinator agent |
| Cloudflare: Worker + Durable Object + D1 + Cron + Workers Builds; public management dashboard, no login | R2 lake, Workflows, Queues, Vectorize, Containers, Access-protected internal views |
| Assistant: Agent SDK on Oracle VM, 4 tools, playbook in D1, embedded in the public dashboard | Workers AI fallback brain, semantic search, Slack front end |

Free-tier facts to verify once (they move): Cloudflare pricing page https://developers.cloudflare.com/workers/platform/pricing/ · Oracle Always Free https://www.oracle.com/cloud/free/ · Agent SDK on a Claude plan https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan

---

# PART B — Build the plant while learning Claude Code (Week 1)

**Goal**: a simulated plant running locally, and your first CLAUDE.md, skill, and subagent — each created because you needed it, not as an exercise.

## B1. Design the plant on paper (30 min)

| Asset | Tags | Failure mode (MVP) | Symptom |
|---|---|---|---|
| GT-1 (gas turbine) | `GT1.EXH_TEMP`, `GT1.CDP` (compressor discharge pressure), `GT1.VIB_B1`, `GT1.LOAD_MW`, `GT1.FUEL_FLOW` | compressor fouling | CDP drifts down, exhaust temp up, over weeks |
| BFP-1, BFP-2 (boiler feed pumps) | `BFPn.VIB`, `BFPn.BRG_TEMP`, `BFPn.DISCH_P`, `BFPn.FLOW`, `BFPn.CURRENT` | bearing wear | vibration and bearing temp rise over ~30 days |
| CTF-1 (cooling tower fan) | `CTF1.VIB`, `CTF1.GBX_TEMP`, `CTF1.CURRENT` | gearbox wear | gearbox temp and vibration rise |

Write this as `docs/plant.md`. It becomes the `maintenance-domain` skill in Part C and the chatbot's playbook in Part E.

## B2. Start the repo and write CLAUDE.md (45 min) — *Lesson 1.2*

```powershell
mkdir learn-cognitive-maintenance; cd learn-cognitive-maintenance; git init; uv init --package; claude
```

In Claude Code: `/init`, then edit `CLAUDE.md` down to what Claude can't infer:

```markdown
# learn-cognitive-maintenance — cognitive maintenance MVP
## Environment
- Python via `uv run`; tests via `uv run pytest -q`
- Windows native; use forward-slash paths
## Data rules
- `data/raw/` is read-only. Derived data goes to `data/derived/`.
- Ground truth (`failures`, hidden health) is never used as a model input. Only the validator may read it.
- Tag naming: `<ASSET>.<TAG>`, e.g. `GT1.EXH_TEMP`. Asset list in docs/plant.md.
## Reproducibility
- Every training run has a seed and a run id; artefacts are JSON in `models/artifacts/`.
## Never
- No `pip install` — use `uv add`. Never commit notebooks with outputs. Never write to `data/raw/`.
```

**Learn from**: memory/CLAUDE.md https://code.claude.com/docs/en/memory · plan mode https://code.claude.com/docs/en/permission-modes · Anthropic Academy *Claude Code in Action* (free, ~3 h) https://anthropic.skilljar.com/claude-code-in-action

## B3. The simulator (3 h) — first real Claude Code task

Ask Claude Code, in plan mode:

> Build `plant/sim.py`: a deterministic simulator where every sensor value is a pure function of `(seed, asset, tag, sim_time)`. Each asset has a hidden health index 1→0 driven by a scripted scenario in `plant/faults.yaml`; sensors = baseline + f(1−health) + load effect + noise. Include a `failures()` function returning ground truth. Write pytest tests: seed reproducibility, health monotonic between repairs, symptoms respond to health. Read docs/plant.md and CLAUDE.md first.

`plant/faults.yaml`:

```yaml
seed: 42
start: 2024-01-01
horizon_days: 365
scenarios:
  - asset: BFP-2
    mode: bearing_wear
    onset_day: 240
    duration_days: 30
  - asset: GT-1
    mode: compressor_fouling
    onset_day: 270
    duration_days: 60
  - asset: CTF-1
    mode: gearbox_wear
    onset_day: 300
    duration_days: 25
  - asset: BFP-1
    event: sensor_outage
    from_day: 200
    to_day: 203
```

Also ask for realism: a dead (constant) tag, a few missing hours, one duplicated timestamp. Real data has these; your `data` subagent should find them.

## B4. The plant API (2 h)

Ask Claude Code for a FastAPI app `plant/api.py` over the simulator, with a **simulated clock** and **two tokens**:

```
# Clock
GET  /clock                          → {"sim_time": "...", "speed": 60}
POST /clock/speed   {"speed": 3600}  # 1 real second = 1 sim hour
POST /clock/jump    {"to": "2024-09-01T00:00"}

# Plant routes — READ token
GET  /assets
GET  /tags/{tag}/history?from=&to=&interval=1h
GET  /tags/latest?asset_id=
GET  /maintenance/log?asset_id=
POST /maintenance/workorder

# Admin routes — ADMIN token (ground truth; only you and the validator)
GET  /admin/ground_truth
POST /admin/inject_fault
POST /admin/reset {"seed": 42}
POST /admin/model_artifacts        # Part D: Colab uploads models here
```

Design note that matters later: the clock is the **only** stateful thing. Everything else is computed from `(seed, asset, tag, sim_time)`. That's what makes moving it to Cloudflare (Part D) almost free.

Run it: `uv run uvicorn plant.api:app`, jump the clock to day 250, `curl` `/tags/latest?asset_id=BFP-2` and watch vibration climbing.

**Learn from**: FastAPI https://fastapi.tiangolo.com

## B5. Your first skill: `/eda` (1 h) — *Lesson 1.3*

You'll profile plant data many times; that's a skill. `.claude/skills/eda/SKILL.md`:

```markdown
---
description: Profiles a plant dataset (parquet/CSV/DuckDB) and writes a findings report. Use when the user says "profile", "EDA", "explore", or a new data file appears.
---
Given a dataset path:
1. Load with pandas (polars if >2 GB). Report shape, dtypes, time range, assets and tags present.
2. Per tag: null %, constant?, min/median/max, largest gap in timestamps, duplicate timestamps.
3. Flag likely leakage: any column named like health, failure, rul, or dated after the current sim time.
4. Write reports/eda_<name>.md with **Findings** (max 10 bullets) and **Recommended fixes**.
5. Never modify data files. Return only the Findings section to the conversation.
```

Restart `claude`, export a month of data with a small script (`scripts/export.py` → `data/raw/readings_2024-08.parquet`), run `/eda data/raw/readings_2024-08.parquet`. It should find the dead tag and the outage.

**Learn from**: skills https://code.claude.com/docs/en/skills · official skills repo (real SKILL.md files) https://github.com/anthropics/skills · Anthropic Academy *Introduction to Agent Skills* https://anthropic.skilljar.com

## B6. Your first subagent: `data` (1.5 h) — *Lessons 1.1 + 1.4*

Profiling floods your context with tables. That's a subagent. `.claude/agents/data.md`:

```markdown
---
name: data
description: Profiles, queries, and cleans plant data and builds feature tables. Use before any modelling or when a new tag, asset, or file appears.
tools: Read, Grep, Glob, Bash
model: sonnet
memory: project
---
You are the plant's data engineer. Read from data/raw and the plant API via `uv run plantctl` (read token only).
Never write to data/raw. Derived tables go to data/derived. Never read ground truth.
Return at most 12 bullets; put full tables in reports/. Update your memory with dataset facts (dead tags, known gaps, tag quirks).
```

Also ask Claude Code for `plantctl`, a small Python CLI over the API (`uv run plantctl history --tag BFP2.VIB --from ... --to ...`, `plantctl assets`, `plantctl workorder ...`). Subagents call it via Bash, so a hook can police it.

Test three things:
1. *"Use the data subagent to profile August."* Only a summary should land in your conversation (`/tasks` shows it running).
2. *"Use the data subagent to delete the dead tag from data/raw."* It must refuse.
3. Run it twice; look in `.claude/agent-memory/data/` at what it learned.

**Learn from**: subagents (read the *Data scientist* and *Database query validator* examples) https://code.claude.com/docs/en/sub-agents · context window https://code.claude.com/docs/en/context-window · Anthropic Academy *Introduction to Subagents* https://anthropic.skilljar.com

## B7. Guardrail hook (30 min)

`.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash|Write|Edit",
        "hooks": [{ "type": "command", "command": "bash scripts/guard-raw-data.sh" }] }
    ],
    "PostToolUse": [
      { "matcher": "Edit|Write",
        "hooks": [{ "type": "command", "command": "bash scripts/lint-test.sh" }] }
    ]
  }
}
```

`guard-raw-data.sh` reads the tool call JSON from stdin and exits 2 (block) if the command or path targets `data/raw`. Ask Claude Code to write both scripts. Then try to make it write to `data/raw` — the hook, not the prompt, stops it.

**Learn from**: hooks https://code.claude.com/docs/en/hooks

## Checkpoint B
- `uv run pytest` green; API runs; clock jumps; BFP-2 vibration visibly rises after day 240.
- `/eda` finds the dead tag and the outage.
- `data` subagent refuses to touch `data/raw`; the hook blocks it even if asked directly.
- You can explain, in one sentence each, why `/eda` is a skill and `data` is a subagent.

---

# PART C — The dev-time team and the two MVP models (Week 2–3)

**Goal**: one `/run` command that takes a maintenance question through data → model → validation, with Claude Code subagents doing the work. — *Lesson 2.1*

## C1. The team

| Subagent | Owns | Tools | May reach | Must never |
|---|---|---|---|---|
| **data** (built) | profile, query, clean, feature tables | Read, Grep, Glob, Bash | plant API read token; `data/raw`, `data/derived` | write to raw; read ground truth |
| **modeler** | write/test model code in `models/`, prepare the Colab notebook, evaluate artefacts | Read, Edit, Write, Bash, Glob, Grep; `isolation: worktree` | `data/derived`, `models/` | query the plant API; deploy; invent numbers |
| **validator** | leakage, time-split, backtest sanity, code review → GO/NO-GO | Read, Grep, Glob, Bash | plant API **admin** token (ground truth) | edit anything |
| **deployer** | `wrangler deploy --env staging`, upload artefacts, trigger scoring | Read, Bash; `permissionMode: default` | Cloudflare API token (scoped); Cloudflare MCP server | hold prod token; deploy prod without an approval file |

The orchestrator (`/run`) needs only Read, Glob, and the `Agent` tool. It never touches data itself.

Mapping to your diagram: Research + Data Analyst → `data` · QA → `validator` · PM → `/run`'s framing step · Design/GTM → reporter (Later).

## C2. Domain and method skills (1.5 h)

The four maintenance tasks are **skills, not subagents**: they are knowledge (how to do X); `modeler` is the worker that reads them.

`.claude/skills/maintenance-domain/SKILL.md` — generated from `docs/plant.md`: assets, tags, failure modes with symptoms, maintenance-log schema. Preloaded into `data`, `modeler`, `validator` via `skills:` in their frontmatter.

`.claude/skills/anomaly/SKILL.md` (no training):
```markdown
---
description: Detects abnormal sensor behaviour per asset with rolling z-scores and control limits. Use for "is X drifting", "which assets look abnormal", or /run anomaly.
---
Method: for each tag, 30-day rolling mean/std computed only on data before the current sim time; flag |z| > 3 for 6+ consecutive hours; severity = max z. Exclude dead tags and outage windows.
Output: reports/anomaly_<date>.md with a table asset, tag, first_flag_ts, severity, and a one-line interpretation using the failure modes in maintenance-domain. Implementation lives in models/anomaly.py; call `uv run python -m models.anomaly score --as-of <ts>`. Never hand-compute.
```

`.claude/skills/predict/SKILL.md` (Colab-trained):
```markdown
---
description: Predicts probability of failure within N days per asset using logistic regression on lagged features. Use for "which assets will fail", risk ranking, or /run predict.
---
Features: per tag — current value, 7-day mean, 7-day slope, 30-day slope, hours since last repair. Label: failure within horizon from ground truth — labels are built ONLY by the validator-approved script models/predict/labels.py, never by hand.
Split: time-based; train on data before cutoff, test on the replay window. Metrics: PR-AUC, precision@5, lead time in days.
Training: Colab notebook notebooks/train_predict.ipynb (thin: install repo, pull features, call models.predict.train(), upload artefact JSON via POST /admin/model_artifacts).
Artefact: JSON with feature names, scaler stats, coefficients, metrics, run id. Scoring: `uv run python -m models.predict score --artifact <id> --as-of <ts>`.
```

## C3. `modeler` and `validator` (1.5 h)

`.claude/agents/modeler.md`:
```markdown
---
name: modeler
description: Writes and tests maintenance model code and prepares Colab training runs for a task type (anomaly or predict). Use when the orchestrator names a task and a prepared feature table.
tools: Read, Edit, Write, Bash, Glob, Grep
model: sonnet
isolation: worktree
maxTurns: 40
skills: [maintenance-domain, anomaly, predict]
---
You build ML code, not numbers. Follow the named task skill exactly. Data comes from data/derived (prepared by the data subagent). Heavy training runs in Colab: prepare the notebook cell and the feature export, then stop and tell the user to run it; continue when they give you the artefact id. To score, always call the model CLI. Return: run id, config, metrics, backtest error, one sentence vs last run.
```

`.claude/agents/validator.md`:
```markdown
---
name: validator
description: Adversarial checker for any run. Checks leakage, time-based split, contamination, metric correctness, and compares predictions with ground truth. Returns GO or NO-GO with evidence. Must run before any deployment.
tools: Read, Grep, Glob, Bash
model: opus
skills: [maintenance-domain, anomaly, predict]
---
You never edit files. Checklist: (1) no ground-truth or future readings in features, (2) split is time-based and after the last repair, (3) metrics computed on the replay window only, (4) alerts/predictions compared to `uv run plantctl --admin ground-truth` — report lead time per scripted failure, false alerts per asset-month, (5) code reads cleanly and tests pass. Output GO or NO-GO, then evidence per item.
```

## C4. The orchestrator `/run` (1 h)

`.claude/skills/run/SKILL.md`:
```markdown
---
description: Full maintenance workflow for /run <anomaly|predict> <asset|fleet> <horizon>. Use only when invoked by the user.
disable-model-invocation: true
---
1. FRAME — restate question, metric, sim-time window. Wait for approval.
2. DATA — data subagent prepares data/derived/<task>_<target>_<asof>.parquet and reports gaps.
3. MODEL — modeler subagent applies skill <task>. For predict, pause while the user runs Colab. Max 3 rounds.
4. VALIDATE — validator subagent. NO-GO → back to 2 or 3 with its findings. Never skip.
5. ASK — "Deploy this run to staging?" Yes → deployer subagent (Part D). No → stop.
6. Summarise in 5 bullets: question, answer, confidence, caveats, next step.
```

Invoke: `/run anomaly fleet 7d` · `/run predict fleet 30d`

## C5. Train `predict` in Colab (2 h)

1. Push the repo to GitHub as `learn-cognitive-maintenance` (private is fine; Colab can install from a private repo with a token, or make it public since it's a learning project).
2. `notebooks/train_predict.ipynb` — about 20 lines:
   ```python
   !pip install -q git+https://github.com/<you>/learn-cognitive-maintenance
   from google.colab import userdata
   from models.predict import train, upload
   feats = ...  # read the parquet the data subagent exported, uploaded to Colab or fetched via the API with userdata.get("PLANT_READ_TOKEN")
   art = train(feats, horizon_days=30, seed=42)
   upload(art, api="https://<staging-url>", token=userdata.get("PLANT_ADMIN_TOKEN"))
   ```
   Tokens live in Colab's Secrets panel, never in cells.
3. All logic is in `models/predict/` so `modeler` can unit-test it locally and Colab just calls it.

**Learn from**: Colab secrets https://colab.research.google.com (Secrets tab in the left sidebar)

## C6. Replay — run it like a real project (ongoing)

Treat day 1–200 as history and day 200–365 as replay. Each session: jump the clock 7 days, `/run anomaly fleet 7d`, `/run predict fleet 30d`, then validator reveals ground truth and scores lead time. Log the results in `reports/replay_log.md`. This is your demo story: "alert on day 247, failure on day 270."

**Learn from**: subagent patterns (chain, parallel, restrict) https://code.claude.com/docs/en/sub-agents · orchestration example https://github.com/shanraisshan/claude-code-best-practice · DeepLearning.AI *Claude Code: A Highly Agentic Coding Assistant* (free, ~2 h) https://deeplearning.ai/short-courses/claude-code-a-highly-agentic-coding-assistant

## Checkpoint C
- `/run anomaly fleet 7d` at sim day 250 flags BFP-2 with a sensible interpretation; validator says GO with measured lead time.
- `/run predict fleet 30d` produces a Colab artefact, and validator confirms the split is time-based and ground truth never entered features.
- Ask `modeler` to query the plant API directly — it shouldn't be able to.
- You can say why `predict` is a skill and `modeler` is a subagent, and why validator sees ground truth but modeler doesn't.

---

# PART D — Deploy on Cloudflare, free (Week 4) — *Lessons 2.2 + 2.3*

**Goal**: the plant, ingestion, nightly scoring, and dashboard run on Cloudflare's free tier, deployed from GitHub, with the `deployer` subagent doing staging deploys.

## D1. The mapping

| Piece | Cloudflare (free) | Notes |
|---|---|---|
| Plant API | **Worker** (TypeScript; or a Python Worker) | pure-function simulator ported from `plant/sim.py` |
| Simulated clock | **Durable Object** with an alarm | the only state; free plan supports DOs |
| Everything tabular: assets, maintenance log, ground truth, readings, predictions, alerts, runs, model_artifacts, playbook | **D1** | 1 year × 12 tags × hourly ≈ 100k rows |
| Ingestion | **Cron Trigger** every minute → D1 | at speed 3600, one real minute = one sim hour |
| Scoring | **Cron Trigger** nightly Worker: z-scores + logistic score in JS from the artefact JSON in D1 | no Containers, no Python at inference |
| Dashboard | Worker with static assets, **public, no login** | management view + embedded chat + demo controls (D3) |
| Auth | read/admin bearer tokens via `wrangler secret put`; the dashboard Worker holds them server-side | no login for viewers; abuse control by rate limiting, not by accounts |
| CI/CD | **Workers Builds** (git-connected) | staging on push to `main`; production on tag |
| Local dev | `wrangler dev` | simulates D1, DO, cron |

`wrangler.toml` gets `[env.staging]` and `[env.production]` with **separate** D1 databases and DO namespaces.

## D2. Steps (about a week)

1. **Port the plant API** (1 day): ask Claude Code to port `plant/sim.py` to `apps/plant-api/src/sim.ts` with the same tests, a Durable Object `Clock` (alarm advances `sim_time` by `speed` every real second), and D1 tables from `apps/plant-api/schema.sql`. `wrangler deploy --env staging`.
2. **Ingest Worker** (½ day): cron `* * * * *` reads `/tags/latest` for all assets at the current sim hour and inserts into D1 `readings`. Idempotent on `(tag, ts)`.
3. **Artefact upload** (½ day): `POST /admin/model_artifacts` stores the Colab JSON in D1; re-run C5 against the staging URL.
4. **Scoring Worker** (1 day): nightly cron (`0 2 * * *`) reads lagged features from D1, computes anomaly z-scores and the logistic probability in JS, writes `predictions` and `alerts`, inserts a `workorders` row when risk > threshold.
5. **Dashboard** (1 day) — see D3 for the spec. Public URL, no login.
6. **CI/CD** (½ day): connect the repo to Workers Builds. Prod token exists only in the Cloudflare/GitHub secret store — never on your PC in a `.env` that could be read by an agent.
7. **`deployer` subagent** (½ day):
   ```markdown
   ---
   name: deployer
   description: Deploys Workers to staging, uploads model artefacts, triggers scoring runs, and checks logs. Use after validator returns GO.
   tools: Read, Bash, Glob
   permissionMode: default
   mcpServers:
     cloudflare: { command: npx, args: ["-y", "@cloudflare/mcp-server-cloudflare"] }
   ---
   You deploy to staging only. Run `wrangler deploy --env staging`, `wrangler d1 execute --env staging`, and `wrangler tail`. Production deploys happen via git tag through CI, never from here. Report the deployed version, D1 row counts, and the last 20 log lines.
   ```
   Add a `PreToolUse` hook that blocks any Bash command containing `--env production` unless `.approvals/prod-<sha>` exists. Then extend `/run` step 5 to call it.
8. **Connectors** (½ day): `claude mcp add --transport http claude-code-docs https://code.claude.com/docs/mcp` for docs; the Cloudflare MCP server only on `deployer` (above). Nothing else for the MVP.

## D3. The management dashboard (public demo, no login)

Audience: plant managers and demo viewers, on a laptop or phone. No accounts — anyone with the link can use every demo feature. One page, `apps/dashboard`, static HTML + a little JS, served by a Worker that also proxies the API so no token ever reaches the browser.

**Layout, top to bottom**

1. **Header**: plant name, current *sim time*, a badge "Demo — simulated data", and a *Demo controls* button.
2. **KPI strip** (4 cards): Assets healthy / warning / critical · Open work orders · Predicted failures next 30 days · Alerts last 7 days.
3. **Fleet board**: one card per asset with a colour status (green/amber/red from the latest risk score and active alerts), risk %, top driver tag, days since last maintenance. Click a card → asset detail.
4. **Asset detail** (drawer): 30-day trend chart for the asset's tags with alert markers, current prediction with its top three drivers, recommended actions from the playbook, and a *Create work order* button (draft → confirm).
5. **Alerts and work orders**: two tables, newest first, filterable by asset.
6. **Ask the plant** (chat panel, always visible on the right or as a bottom sheet on phones): the assistant from Part E. Suggested prompts as chips: "What happened to BFP-2 this week?", "Which assets need attention?", "What should we do about the gearbox alert?".
7. **Demo controls** (modal): *Jump clock +7 days*, *Jump to day N*, *Run scoring now*, *Inject fault* (asset + mode), *Reset plant*. These call `/admin/*` through the dashboard Worker, which adds the admin token server-side.

**How it stays safe without login**
- The browser only ever talks to the dashboard Worker's `/api/*`; that Worker adds the read or admin token and forwards. Tokens never ship to the client.
- Rate limiting per IP in the Worker (KV counter; e.g. 60 requests/min, 20 chat messages/hour, 5 demo-control actions/hour). Cloudflare's free plan also gives you one WAF rate-limiting rule — use it on `/api/chat`.
- Demo controls are idempotent and bounded: the clock can only move forward inside the 1-year horizon; *Reset* restores the scripted `faults.yaml` state.
- `create_workorder` always shows a confirmation step; work orders are tagged `source=demo`.
- The chat endpoint's monthly cost is your Max quota, so the limits above are what keep a public link from draining it. Keep the link unlisted.

**Build order**: KPI strip + fleet board from D1 (half a day) → asset drawer with chart (half a day) → demo controls (2 h) → chat panel wired to the Part E `/chat` endpoint (1 h, once Part E exists). Ask Claude Code to build it with plain HTML/CSS/JS or a single-file Preact page — no framework build step, so Workers Builds stays trivial. Use the `frontend-design` skill in Claude Code if you have it, otherwise: one accent colour, status colours only for status, big numbers on the KPI cards, sentence-case labels.

**Learn from**: Workers https://developers.cloudflare.com/workers/ · Durable Objects alarms https://developers.cloudflare.com/durable-objects/ · D1 https://developers.cloudflare.com/d1/ · Cron Triggers https://developers.cloudflare.com/workers/configuration/cron-triggers/ · Workers Builds https://developers.cloudflare.com/workers/ci-cd/builds/ · wrangler environments https://developers.cloudflare.com/workers/wrangler/environments/ · Python Workers examples https://github.com/cloudflare/python-workers-examples · Cloudflare MCP server https://github.com/cloudflare/mcp-server-cloudflare · Claude Code MCP https://code.claude.com/docs/en/mcp

## Checkpoint D
- Fresh clone → `claude` → `/` shows `/run`, `/eda`, `/anomaly`, `/predict`.
- Staging URL returns live readings; the clock advances on its own; nightly scoring writes alerts you can see on the dashboard.
- The dashboard works from a phone with no login; *Jump clock +7 days* then *Run scoring now* produces a new alert; tokens never appear in the browser's network tab.
- `deployer` can deploy staging; asking it to deploy production is blocked by the hook.
- Staging and production use different D1 databases (check `wrangler.toml`).

---

# PART E — The cognitive maintenance assistant (Week 5) — *Lesson 2.4*

**Goal**: an operator chats with Claude about the plant. Everything before this was *dev-time* (agents that help you build); this is a *run-time* agent inside the product. It reuses the same data, model outputs, and domain knowledge.

Design rule: **the assistant never guesses numbers**. Every fact comes from a tool call; every recommendation names a failure mode and cites evidence (tag, value, time); creating a work order needs the operator's confirmation.

## E1. Where it runs and why

Your Claude Max plan can't be called from a Worker (the API rejects subscription tokens). It *can* power your own program through the **Claude Agent SDK**, authenticated with `claude setup-token`. So the brain runs on an **Oracle Cloud Always Free VM**, reached through a Cloudflare Tunnel; the dashboard Worker is the only client that calls it (with a shared secret header), so no login is needed for viewers and the endpoint is not directly public. Your PC is the demo-day fallback. Re-check the Agent SDK-on-subscription article before demo day; the rules changed and paused in June 2026.

## E2. The four MVP tools

| Operator asks | Tool | Reads |
|---|---|---|
| "Status of BFP-2?" | `get_asset_status(asset_id)` | D1 latest readings, active alerts, risk score |
| "What happened last night?" | `get_events(asset_id, from, to)` | D1 alerts, threshold crossings, prediction changes, work orders |
| "What should we do?" | `get_recommendations(failure_mode, severity)` | D1 `playbook` (from `docs/playbook/*.md`), keyword search |
| "Create a work order" | `create_workorder(...)` — **draft → confirm → execute** | plant API `POST /maintenance/workorder` |

Playbook first: `docs/playbook/<mode>.md` for each of the three failure modes — symptoms, confirming checks, immediate actions, spare parts, typical lead time. Load them into D1 with a script. The playbook alone makes the assistant useful.

## E3. Build it (about a week)

1. **Playbook** (½ day, with Claude Code): the three files above, then `scripts/load_playbook.py` → D1 via the admin API.
2. **`apps/assistant`** on your PC (1 day): Python, Claude Agent SDK, the four tools as Python functions calling the staging plant API with the read token; a FastAPI `/chat` endpoint (JSON in, streamed text out). Chat in a terminal first; the UI is the dashboard's chat panel from D3, not a separate page.
3. **Confirmation flow** (½ day): `create_workorder` returns a draft; the UI shows Confirm; only then is the POST sent.
4. **Oracle VM** (½ day):
   - Create an Always Free instance (Ampere A1 up to 4 OCPU/24 GB; if "out of host capacity", retry or use the AMD Micro shape), Ubuntu, SSH key.
   - `ufw` allow only SSH. Install Node (nvm), `uv`, `npm i -g @anthropic-ai/claude-code`.
   - On your PC: `claude setup-token` → paste into `/etc/assistant.env` on the VM as `CLAUDE_CODE_OAUTH_TOKEN`, plus `PLANT_READ_TOKEN`; `chmod 600`.
   - `git clone`, `uv sync`, systemd unit running the app on `127.0.0.1:8000` with `EnvironmentFile=/etc/assistant.env`, `Restart=always`.
   - `cloudflared tunnel` → `assistant.<your-domain>` → `localhost:8000`. The app requires an `X-Demo-Secret` header that only the dashboard Worker knows (set with `wrangler secret put`); requests without it get 401. Rate limit per IP inside the app as well.
   - Everything in `deploy/oracle/` (setup script, unit file, tunnel config) so it's reproducible.
5. **Golden set** (½ day): from `faults.yaml`, write 5 question/answer pairs per scenario with the true answers. `scripts/eval_assistant.py` runs them against `/chat` and scores: correct failure mode named, numbers match tool output, recommendation matches the playbook. Run it after every prompt change.

**Learn from**: Agent SDK https://code.claude.com/docs/en/agent-sdk/overview · Agent SDK on your plan https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan · headless auth (`claude setup-token`) https://code.claude.com/docs/en/setup · Oracle Free Tier https://www.oracle.com/cloud/free/ · Cloudflare Tunnel https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/ · rate limiting rules https://developers.cloudflare.com/waf/rate-limiting-rules/ · Building effective agents https://www.anthropic.com/research/building-effective-agents

## Checkpoint E
- From the public dashboard's chat panel: "What happened to BFP-2 last week?" returns the real alert with tag, value, and time; "What should we do?" names bearing wear and the playbook actions; "Create a work order" asks for confirmation first.
- Calling `assistant.<your-domain>/chat` directly without the secret header returns 401.
- Golden set score ≥ 80% correct failure mode; zero invented numbers.
- Kill the VM's app; your PC fallback runs the same code with `claude login`.

---

# PART F — Demo, self-assessment, and what comes later

## F1. The 10-minute demo
1. Open the public dashboard on the manager's phone: fleet healthy at sim day 230, KPIs all green.
2. Demo controls → *Jump clock +7 days* three times, then *Run scoring now*. BFP-2 turns amber, then red; an alert appears.
3. In the chat panel, ask what happened and what to do. It cites the vibration trend and the bearing-wear playbook.
4. From the asset drawer, create the work order; confirm.
5. Reveal ground truth (validator or `/admin/ground_truth`): failure was scripted for day 270 — lead time 15 days.
6. Open Claude Code and show `/run anomaly fleet 7d` doing steps 2–3 with the subagents — that's how the models got built.

## F2. Self-assessment — you're done when you can answer without notes
1. For each of `data`, `modeler`, `validator`, `deployer`: why it exists, and one thing it must not be able to do.
2. Why `predict` is a skill and `modeler` is a subagent.
3. What a subagent sees at startup, and what it doesn't.
4. Why validator runs before any deploy, and why it's the only agent that may read ground truth.
5. Why a hook, not a prompt line, protects `data/raw` and production.
6. What a fresh clone of the repo gets, and what it doesn't (tokens, agent memory).
7. Why the assistant's numbers must come from tools.
8. Which pieces are free forever, which are trials, and what would cost money if this became a product.

## F3. Later upgrades (in the order they pay off)
1. `reporter` subagent + `/eval-report` skill → weekly `reports/*.md` and a Slack webhook.
2. Full plant: 14 assets, 8 failure modes, 2 years; `rul` and `forecast` skills; NASA C-MAPSS as a benchmark for the GT class (https://ieee-dataport.org/documents/nasa-turbofan-jet-engine-data-set, walkthrough https://towardsdatascience.com/?p=146471).
3. R2 parquet lake + DuckDB for the `data` subagent when D1 gets big.
4. `assistant-evaluator` subagent so `/run` re-scores the golden set after every model change.
5. Second brain: `apps/assistant-cf` Worker on Workers AI (free) with the same tool schemas, for when the VM is off.
6. Semantic search: Vectorize + Workers AI embeddings over manuals and past work orders.
7. ONNX export + `onnxruntime-web` for tree models in the scoring Worker.
8. Paid, only if it becomes a product: Containers (real Python inference), Queues, Anthropic API for the assistant, Workflows for scoring with retries.

## F4. Final repo layout

```
learn-cognitive-maintenance/
CLAUDE.md
docs/
  plant.md                 assets, tags, failure modes → maintenance-domain skill
  playbook/                one file per failure mode → D1 playbook table
  claude-setup.md          how the team works; written in F1
plant/
  sim.py  api.py  faults.yaml       local simulator + FastAPI (Part B)
apps/
  plant-api/               Worker + Durable Object clock + D1 schema (Part D)
  ingest/  scoring/            cron Workers
  dashboard/               public management page + API proxy + rate limiting (D3)
  assistant/               Agent SDK app; runs on Oracle VM or your PC (Part E)
deploy/oracle/             setup.sh, assistant.service, cloudflared config
models/
  anomaly.py  predict/     model code with CLIs; imported by Colab and tests
  artifacts/               JSON artefacts (also stored in D1)
notebooks/train_predict.ipynb
scripts/
  plantctl.py  export.py  load_playbook.py  eval_assistant.py
  guard-raw-data.sh  lint-test.sh  block-prod-deploy.sh
data/  raw/  derived/
reports/
.claude/
  settings.json            hooks
  skills/  run/ eda/ maintenance-domain/ anomaly/ predict/
  agents/  data.md modeler.md validator.md deployer.md
  agent-memory/data/
```

## F5. Core reading list (bookmark)
- Claude Code docs home https://code.claude.com/docs/en/overview and full index https://code.claude.com/docs/llms.txt
- Sub-agents · Skills · Hooks · MCP · Memory: `https://code.claude.com/docs/en/{sub-agents,skills,hooks,mcp,memory}`
- Anthropic Academy (free courses: Claude Code in Action, Agent Skills, Subagents, MCP) https://anthropic.skilljar.com
- DeepLearning.AI Claude Code course https://deeplearning.ai/short-courses/claude-code-a-highly-agentic-coding-assistant
- Claude Code repo + changelog (when docs and behaviour disagree, read this) https://github.com/anthropics/claude-code
- Best-practice repos with templates https://github.com/MuhammadUsmanGM/claude-code-best-practices · https://github.com/shanraisshan/claude-code-best-practice
- Skills vs subagents vs MCP explained https://smithhorngroup.substack.com/p/choosing-between-skills-subagents
