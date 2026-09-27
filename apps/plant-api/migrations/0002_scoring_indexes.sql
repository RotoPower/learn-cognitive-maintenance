-- Scoring Worker (D2.4). Keep every lookup index-bounded: the free tier bills rows read.
-- source='scoring' marks work orders raised by the scoring Worker.
CREATE INDEX IF NOT EXISTS ix_runs_started ON runs(started);
CREATE INDEX IF NOT EXISTS ix_alerts_open ON alerts(asset_id, kind, status, last_flag_ts);
CREATE INDEX IF NOT EXISTS ix_artifacts_task ON model_artifacts(task, uploaded_at);
