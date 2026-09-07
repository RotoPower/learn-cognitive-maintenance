---
description: Profiles a plant dataset (parquet/CSV/DuckDB) and writes a findings report. Use when the user says "profile", "EDA", "explore", or a new data file appears.
---
Given a dataset path:
1. Load with pandas (polars if >2 GB). Report shape, dtypes, time range, assets and tags present.
2. Per tag: null %, constant?, min/median/max, largest gap in timestamps, duplicate timestamps.
3. Flag likely leakage: any column named like health, failure, rul, or dated after the current sim time.
4. Write reports/eda_<name>.md with **Findings** (max 10 bullets) and **Recommended fixes**.
5. Never modify data files. Return only the Findings section to the conversation.
