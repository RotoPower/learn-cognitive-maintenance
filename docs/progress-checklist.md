# Progress checklist — claude-code-learning-module-data-science.md

Audited 2026-09-08 against the repo, the working tree, and the local setup. Re-checked later the same day after B5.
Legend: `[x]` done and verified · `[~]` partly done · `[ ]` not started · **?** cannot verify from the repo.

## Part A — Setup

- [x] Claude Code installed (2.1.263), `uv` installed (0.11.19), Git present
- [x] `git config --global core.autocrlf input`
- [x] `wrangler` installed (4.129.0)
- [x] `CLAUDE_CODE_GIT_BASH_PATH` pin: not needed on this machine (Git Bash is the only bash on PATH and the Bash tool works); skipped by decision
- [x] Conventions followed: forward-slash paths, `bash scripts/x.sh` hooks, Python CLIs
- [x] A3/A4 reading done

## Part B — Plant, first skill, first subagent, first hook

| Step | Status | Evidence |
|---|---|---|
| B1 `docs/plant.md` | [x] | 4 assets, 24 tags, 3 modes, quirks documented. Tag names differ from the module's sketch (`VIB_DE` not `VIB`, `CDP` kept) |
| B2 repo + `CLAUDE.md` | [x] | CLAUDE.md matches the module text |
| B3 `plant/sim.py` + `faults.yaml` + tests | [x] | pure `(seed, asset, tag, t)`, `failures()`, dead tag, 5 missing hours, 1 duplicate ts, outage; `tests/test_sim.py` |
| B4 `plant/api.py` sim clock + two tokens | [x] | all 12 routes plus `GET /`, `GET /admin/model_artifacts`; `tests/test_api.py`. Uncommitted: Swagger Authorize button, dotenv, `.env.example` |
| B4 "jump to day 250, watch vibration climb" | [x] | anomaly run on 2024-09-20 shows `BFP2.VIB_DE` z up to 5.1 |
| B5 `/eda` skill | [x] | `.claude/skills/eda/SKILL.md`; `reports/eda_30d.md` produced |
| B5 `scripts/export.py` -> `data/raw/readings_2024-08.parquet` | [x] | `export.py` written; you ran it: `data/raw/readings_2024-07.parquet` and `readings_2024-08.parquet` exist. `/eda` on both: dead tag found in each, BFP1 outage found in July (it is not in August with this `faults.yaml`) |
| B6 `data` subagent | [x] | `.claude/agents/data.md`; memory written to `.claude/agent-memory/data/` (2 files) |
| B6 `plantctl` CLI | [x] | `plant/cli.py` + `scripts/plantctl.py` shim, `uv run plantctl` entry point; READ commands and `--admin` commands (explicit flag, so a hook can police it); `tests/test_cli.py` (9 tests) |
| B6 test 1: `data` profiles August, only a summary returns | [x] | 8 bullets came back; full table in `reports/data_readings_2024-08.md`; agent used `plantctl clock` for the leakage check |
| B6 test 2: `data` refuses to delete from raw | [x] | refused on the prompt alone (0 tool calls), citing CLAUDE.md; offered a `data/derived` copy instead |
| B6 test 3: run twice, inspect `.claude/agent-memory/data/` | [x] | second run (July) found the outage; memory corrected its stale "no raw dir / no plantctl" note, recorded the CLI flag names and a hook false-positive workaround |
| B7 hooks in `.claude/settings.json` | [x] | `guard-raw-data.sh` (18-case battery, live-blocked Write and Bash) and `lint-test.sh` (runs pytest after every .py edit) |

**Checkpoint B**
- [x] `uv run pytest` green (73 tests); API runs; clock jumps; BFP-2 vibration rises after day 240
- [x] `/eda` finds the dead tag and the outage (`reports/eda_readings_2024-07.md`, `eda_readings_2024-08.md`, `eda_30d.md`)
- [x] hook blocks writes to `data/raw` even when asked directly (verified this session)
- [ ] one-sentence explanations of skill vs subagent (personal; **?**)

## Part C — Dev-time team and the two models

| Step | Status | Evidence |
|---|---|---|
| C1 team table | [~] | `data`, `modeler`, `validator` exist; `deployer` is Part D |
| C2 `maintenance-domain` skill, preloaded via `skills:` | [x] | generated from `docs/plant.md`, in all three agents' frontmatter |
| C2 `anomaly` skill + `models/anomaly.py` | [x] | scorer with CLI, `tests/test_anomaly.py`, `reports/anomaly_2024-09-20.md`, artefact JSON |
| C2 `predict` skill + `models/predict/` | [x] | `features.py`, `labels.py` (validator-only), `model.py`, CLI, `tests/test_predict.py` (26 tests) |
| C3 `modeler` and `validator` agents | [x] | frontmatter exactly as in the module |
| C4 `/run` orchestrator skill | [x] | `disable-model-invocation: true`. Note: name collides with the built-in `run` skill |
| C5.1 push to GitHub | [x] | `RotoPower/learn-cognitive-maintenance`, public, 3 commits |
| C5.2 `notebooks/train_predict.ipynb` | [x] | thin, 4 cells, no outputs; installs from GitHub (packaging fixed) |
| C5.2 Colab run completed | [x] | `models/artifacts/predict_fleet_h30_2024-10-15_s42.json` trained in Colab 2026-09-08 13:44Z; cutoff 2024-10-15, 916 train rows / 184 replay rows, embargo 30 d. Replay: PR-AUC 0.24, 0 of 2 failures alerted, 6 false alerts per asset-month. Weak by construction: training saw only BFP2 bearing wear; replay holds GT1 fouling and CTF1 gearbox wear |
| C5.2 artefact uploaded to the API | [x] | server restarted with `.env` tokens; `plantctl --admin artifacts` lists both `anomaly_fleet_2024-09-20_r1` and `predict_fleet_h30_2024-10-15_s42` |
| C6 replay log `reports/replay_log.md` | [x] | `scripts/replay.py` runs 24 weekly sessions (day 200-361): clock jump, anomaly score, predict score, log row; never reads ground truth. Validator appended GO/NO-GO with lead times and false-alert rates. Per-session reports in `reports/replay/` |

**Checkpoint C**
- [x] anomaly flags BFP-2 with a sensible interpretation; validator says **GO** with measured lead time: first flag day 259 (2024-09-16), failure day 270, **11 days lead** (7 by weekly cadence); CTF1 also caught (10 days); GT1 slow fouling missed (known gap); 0.09 false alerts per asset-month
- [x] predict produces a Colab artefact and the validator confirms the split is time-based (cutoff day 288, 30-day embargo) and ground truth never entered features. Verdict **NO-GO on quality**: 0 of 2 out-of-sample failures caught, 0.58 false alerts per asset-month, `hours_since_repair` acting as a clock proxy. Retrain with more failure examples before Part D scoring
- [ ] `modeler` cannot query the plant API (no `plantctl`, no `curl` restriction tested; **?**)
- [ ] the four "why" questions (personal; **?**)

## Part D — Cloudflare

- [ ] D2.1 port `plant/sim.py` to a Worker + Durable Object clock + D1 schema (`apps/plant-api/`)
- [ ] D2.2 ingest Worker
- [ ] D2.3 artefact upload to D1
- [ ] D2.4 scoring Worker
- [ ] D2.5 / D3 public dashboard (`apps/dashboard/`)
- [ ] D2.6 Workers Builds CI/CD
- [ ] D2.7 `deployer` agent + `scripts/block-prod-deploy.sh` hook
- [ ] D2.8 `claude mcp add` for Claude Code docs and Cloudflare (only Slack, Drive, Canva connectors present)
- [ ] Checkpoint D

## Part E — Assistant

- [ ] E3.1 `docs/playbook/<mode>.md` x3 + `scripts/load_playbook.py`
- [ ] E3.2 `apps/assistant/` (Agent SDK, four tools, `/chat`)
- [ ] E3.3 confirmation flow for work orders
- [ ] E3.4 Oracle VM + `deploy/oracle/` + Cloudflare Tunnel
- [ ] E3.5 golden set + `scripts/eval_assistant.py`
- [ ] Checkpoint E

## Part F

- [ ] F1 demo script rehearsed; `docs/claude-setup.md` written
- [ ] F2 self-assessment
- F4 layout: present `CLAUDE.md docs/plant.md plant/ models/ notebooks/ scripts/export.py data/raw data/derived reports/ .claude/`; missing `docs/playbook docs/claude-setup.md apps/ deploy/ scripts/plantctl.py scripts/load_playbook.py scripts/eval_assistant.py scripts/block-prod-deploy.sh .claude/agents/deployer.md`

## Deviations from the module worth knowing

1. **`data/raw/` is populated by hand, by design.** The simulator writes to `data/sim/`; `scripts/export.py` produces monthly historian-style parquet, and only a human runs it into `data/raw/` (the guard hook blocks Claude). `data/raw/` now holds July and August 2024. Decide whether to gitignore it (regenerable) or commit it; it is currently untracked. The `data` agent's memory still says "no raw dir exists" and will correct itself on its next run.
2. **API tokens now consistent.** The server on port 8000 runs with `.env` tokens (default `read-token` is rejected), and `plantctl` loads the same `.env`, so agents need no URL or token overrides. Note the server clock restarted at 2024-01-01; jump it (`plantctl --admin jump --to 2024-09-20`) before any run that needs "current sim time" in the replay window.
3. **Tag names** in `docs/plant.md` are richer than the module's sketch; the maintenance-domain skill and all code use the richer set consistently, so this is fine, but the dashboard and playbook in Parts D/E should use these names.
4. **`/run` name** shadows a built-in Claude Code skill. Consider `/pipeline`.
5. **Default `faults.yaml` gives one failure per mode**, so `predict` trained on the scripted year sees only bearing wear before any sensible cutoff. Use `/admin/inject_fault` or a richer scenario file for training data (the test suite already does this).

## Suggested order for what is left in Parts B–C

1. ~~`plantctl`~~ done
2. ~~Restart the API with `.env`; upload the predict artefact~~ done
3. ~~Validator on both runs; replay log~~ done: anomaly GO, predict NO-GO (see `reports/replay_log.md`)
3b. To turn predict into a GO: inject earlier GT1/CTF1 faults (or a richer `faults.yaml`), rebuild the table, drop or normalise `hours_since_repair`, retrain, re-run `scripts/replay.py` and the validator
3c. Anomaly known gap: GT1 slow fouling needs a slope/CUSUM or fixed-reference detector; and reset baselines at logged repairs to kill trailing false alerts
4. Commit the pending API changes (`.env.example`, Swagger auth, dotenv)
5. Decide on `data/raw/` vs `data/sim/` and align the agent prompts
