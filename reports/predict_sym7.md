# Predict retrain: `predict_fleet_h30_2024-01-01_s42_sym7`

Validator verdict (2026-09-27): **GO for the simulated plant.** Replaces
`predict_fleet_h30_2024-10-15_s42` (NO-GO: 0 of 2 out-of-sample failures, `hours_since_repair`
acting as a clock proxy).

## What changed

| | before | now |
|---|---|---|
| training data | 2024 before the cutoff: one failure (BFP2) | a separate year, `plant/faults_history_2023.yaml` (seed 7): 7 failures, all three modes |
| test data | 2024 after 2024-10-15 | all of 2024 (the demo plant), plus a 2025 holdout |
| features | 62: current, 7-day mean, 7/30-day slopes for every tag, `hours_since_repair` | 10: 7-day slopes of the documented symptom tags |
| windows | cross repairs | reset at every corrective repair in the CMMS log (`maintenance_log.json`) |
| alert rule | one daily score above threshold | two consecutive daily scores above threshold |
| L2 | 1 | 10 |

Pipeline: `scripts/prepare_predict_table.py` (features from sensors + CMMS log, never ground
truth) -> `models.predict.labels` (validator-approved, the only reader of ground truth) ->
`uv run python -m models.predict train --table data/derived/predict_fleet_2023-2024.parquet
--cutoff 2024-01-01 --l2 10 --persistence 2 --feature-set symptom_slope7d --reset-at-repairs`.

## Results (held-out only, recomputed by the validator)

| year | failures caught | lead time, days | false alerts per asset-month | PR-AUC |
|---|---|---|---|---|
| 2024 (test) | 3 of 3 | BFP2 14, CTF1 12, GT1 27 | 0.025 | 0.756 |
| 2025 (holdout) | 5 of 5 | 10 to 24, mean 14.6 | 0 | 0.748 |

GO bar: at least 2 of 3 caught, lead time over 7 days, under about 0.2 false alerts per
asset-month. The one 2024 false alert is GT1 one day before its 30-day label window: an early
true alert.

## Caveats

- **Selection bias.** About 40 configurations were compared on 2024 before freezing; the 2024
  false-alert rate is optimistic. The 2025 holdout was generated and scored once, after the
  freeze (`plant/faults_holdout_2025.yaml`, seed 2025), and agrees.
- **Thin margin.** Threshold 0.26; the highest non-failure score in 2025 is 0.27 (CTF1). The
  2-day persistence is what keeps it from alerting. Watch false alerts once live.
- **One simulator.** All three years share the simulator's gains and degradation shapes (noise
  is independent per seed). This shows generalisation across runs of this simulator, not to a
  real plant: expect shorter lead times and more false alerts on real data.

## Deployment conditions (met in `apps/scoring`)

The scoring Worker uses exactly the artefact's 10 features, scaler, coefficients, threshold;
resets windows at every past corrective repair from `/maintenance/log`; alerts only after two
consecutive daily scores; parity with Python checked on two cases, before and after the BFP2
repair (`apps/scoring/test/fixtures/scoring.json`). The artefact is pinned
(`PREDICT_ARTIFACT`), so a newer unvalidated upload is never acted on. The Python CLI refuses to
score this artefact from a saved feature file (it cannot prove the windows were reset).
