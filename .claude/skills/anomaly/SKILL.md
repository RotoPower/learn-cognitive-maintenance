---
description: Detects abnormal sensor behaviour per asset with rolling z-scores and control limits. Use for "is X drifting", "which assets look abnormal", or /run anomaly.
---
Method: for each tag, 30-day rolling mean/std computed only on data before the current sim time; flag |z| > 3 for 6+ consecutive hours; severity = max z. Exclude dead tags and outage windows.
Output: reports/anomaly_<date>.md with a table asset, tag, first_flag_ts, severity, and a one-line interpretation using the failure modes in maintenance-domain. Implementation lives in models/anomaly.py; call `uv run python -m models.anomaly score --as-of <ts>`. Never hand-compute.
