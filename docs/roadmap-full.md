# Roadmap: from the MVP to the full plant (module Part F3, free tier only)

Goal: a complete demo, still simulated and still free. Decided 2026-09-27: follow the
module's F3 list; nothing paid (no Containers, Queues, Workflows, paid API); the `reporter`
subagent and Slack reports are **on hold**.

| phase | scope | done when |
|---|---|---|
| 0 | Staging after the D1 reset: junk rows deleted, migrations 0002-0004, GO predict artefact, playbook loaded, one scoring pass, Colab round-trip (D2.3); **10-minute demo rehearsed**; Checkpoints D and E | the demo runs end to end on staging, chat included |
| 1 | **Full plant**: 14 assets, 8 failure modes, 2 years (design: `docs/design/full-plant.md`); simulator + TypeScript parity, docs/plant.md, maintenance-domain skill, 5 new playbooks, dashboard for 14 assets, D1 budget redesign | staging shows 14 assets; the 2024 numbers of the 4 original assets are unchanged |
| 2 | **`assistant-evaluator`** subagent: re-scores the golden set after every model or prompt change, called from `/pipeline`; golden set extended to the new modes | a model change cannot ship without a golden-set score |
| 3 | **Models**: anomaly and predict for the new modes (re-validated), **RUL** and **forecast** skills and models, NASA C-MAPSS benchmark for the GT class (Colab) | validator GO on each; the dashboard shows remaining useful life |
| 4 | **Assistant without the PC**: `apps/assistant-cf` on Workers AI (free) with the same four tool schemas, used when the PC tunnel is off; semantic search (Vectorize + Workers AI embeddings) over playbooks and work orders | the chat answers outside demo sessions; its golden-set score is measured and shown |
| 5 | **Data lake**: monthly readings to R2 as parquet, DuckDB for the `data` agent | D1 holds only the recent window |
| on hold | `reporter` subagent, weekly reports, Slack | - |

Free-tier constraints that shape the plan:

- **D1 writes (100k rows/day).** 82 tags at clock speed 60 would write ~118k rows/day even without the
  readings index. Phase 1 drops `ix_readings_ts` and runs staging at speed 30 (see the design doc).
- **Worker size (3 MB free).** `onnxruntime-web` does not fit; tree models from Phase 3 are exported to
  JSON and evaluated in plain JS, like the logistic model today.
- **Workers AI (daily free allocation)** covers a demo's fallback-chat and embedding traffic; quality is
  measured with the same golden set as Claude.
