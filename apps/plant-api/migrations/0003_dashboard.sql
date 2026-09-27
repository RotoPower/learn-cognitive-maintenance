-- Dashboard Worker (D3). Demo controls and work orders from the public page are rate
-- limited per viewer (hashed IP) and globally; each action is one row here.
CREATE TABLE IF NOT EXISTS demo_actions (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  kind   TEXT NOT NULL,                 -- jump | score | inject | reset | workorder
  viewer TEXT NOT NULL,                 -- sha-256 prefix of the client IP, never the IP
  ts     TEXT NOT NULL                  -- real time, ISO
);
CREATE INDEX IF NOT EXISTS ix_demo_viewer ON demo_actions(viewer, ts);
CREATE INDEX IF NOT EXISTS ix_demo_kind ON demo_actions(kind, ts);
-- Overview reads: latest scoring run per task, newest alerts.
CREATE INDEX IF NOT EXISTS ix_runs_task_asof ON runs(task, as_of);
CREATE INDEX IF NOT EXISTS ix_alerts_last ON alerts(last_flag_ts);
