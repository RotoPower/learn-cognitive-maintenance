---
description: Full maintenance workflow for /run <anomaly|predict> <asset|fleet> <horizon>. Use only when invoked by the user.
disable-model-invocation: true
---
1. FRAME — restate question, metric, sim-time window. Wait for approval.
2. DATA — data subagent prepares data/derived/<task>_<target>_<asof>.parquet and reports gaps.
3. MODEL — modeler subagent applies skill <task>. For predict, pause while the user runs Colab. Max 3 rounds.
4. VALIDATE — validator subagent. NO-GO → back to 2 or 3 with its findings. Never skip.
5. ASK — "Deploy this run to staging?" Only after a validator GO. Yes → deployer subagent: upload the artefact to staging and confirm the round-trip, redeploy any Worker whose code changed (`npm run deploy:staging`), run one scoring pass, report version, health, D1 counts, last 20 log lines. Production is never deployed from here (git tag + CI; blocked by scripts/block-prod-deploy.sh). No → stop.
6. Summarise in 5 bullets: question, answer, confidence, caveats, next step.
