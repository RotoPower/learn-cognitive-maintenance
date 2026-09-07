---
description: Predicts probability of failure within N days per asset using logistic regression on lagged features. Use for "which assets will fail", risk ranking, or /run predict.
---
Features: per tag — current value, 7-day mean, 7-day slope, 30-day slope, hours since last repair. Label: failure within horizon from ground truth — labels are built ONLY by the validator-approved script models/predict/labels.py, never by hand.
Split: time-based; train on data before cutoff, test on the replay window. Metrics: PR-AUC, precision@5, lead time in days.
Training: Colab notebook notebooks/train_predict.ipynb (thin: install repo, pull features, call models.predict.train(), upload artefact JSON via POST /admin/model_artifacts).
Artefact: JSON with feature names, scaler stats, coefficients, metrics, run id. Scoring: `uv run python -m models.predict score --artifact <id> --as-of <ts>`.
