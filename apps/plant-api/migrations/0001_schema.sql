-- Plant API D1 schema (Part D). One database per environment (staging / production).
-- Everything tabular lives here; the Durable Object holds only the clock and the
-- scenario overlay (seed, injected faults).

CREATE TABLE IF NOT EXISTS assets (
  asset_id    TEXT PRIMARY KEY,           -- canonical id, no hyphen: GT1, BFP1, BFP2, CTF1
  description TEXT NOT NULL,
  tags        TEXT NOT NULL               -- JSON array of full tag names
);

-- CMMS: corrective repairs are derived from the script at reset; work orders are posted.
CREATE TABLE IF NOT EXISTS maintenance_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  kind          TEXT NOT NULL,            -- 'corrective_repair' | 'workorder'
  wo_id         TEXT,                     -- WO-00001 for work orders
  asset_id      TEXT NOT NULL,
  ts            TEXT NOT NULL,            -- sim time ISO (repair time, or when the WO was raised)
  type          TEXT,                     -- workorder type
  description   TEXT NOT NULL DEFAULT '',
  scheduled_for TEXT,
  status        TEXT,                     -- 'open' | 'done'
  source        TEXT NOT NULL DEFAULT 'api'
);
CREATE INDEX IF NOT EXISTS ix_maint_asset_ts ON maintenance_log(asset_id, ts);

-- Ground truth for the CURRENT script (rewritten on reset / inject). ADMIN only.
CREATE TABLE IF NOT EXISTS ground_truth (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  kind      TEXT NOT NULL,                -- 'failure' | 'event'
  asset_id  TEXT NOT NULL,
  mode      TEXT,                         -- failure mode, or event name
  onset     TEXT,
  failure   TEXT,                         -- = repair time
  from_ts   TEXT,
  to_ts     TEXT
);

-- Hourly historian rows written by the ingest Worker (D2.2). Idempotent on (tag, ts).
CREATE TABLE IF NOT EXISTS readings (
  tag   TEXT NOT NULL,
  ts    TEXT NOT NULL,
  value REAL,                             -- NULL during an outage
  PRIMARY KEY (tag, ts)
);
CREATE INDEX IF NOT EXISTS ix_readings_ts ON readings(ts);

-- Model artefacts uploaded from Colab / plantctl (JSON as text).
CREATE TABLE IF NOT EXISTS model_artifacts (
  run_id      TEXT PRIMARY KEY,
  task        TEXT,
  seed        INTEGER,
  uploaded_at TEXT NOT NULL,
  sim_time    TEXT,
  body        TEXT NOT NULL
);

-- Scoring outputs (D2.4).
CREATE TABLE IF NOT EXISTS runs (
  run_id    TEXT PRIMARY KEY,
  task      TEXT NOT NULL,                -- 'anomaly' | 'predict'
  as_of     TEXT NOT NULL,
  artifact  TEXT,
  started   TEXT NOT NULL,
  finished  TEXT,
  summary   TEXT                          -- JSON
);

CREATE TABLE IF NOT EXISTS predictions (
  run_id   TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  as_of    TEXT NOT NULL,
  p_fail   REAL NOT NULL,
  horizon_days INTEGER NOT NULL,
  drivers  TEXT,                          -- JSON
  PRIMARY KEY (run_id, asset_id)
);

CREATE TABLE IF NOT EXISTS alerts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT,
  asset_id      TEXT NOT NULL,
  tag           TEXT,
  kind          TEXT NOT NULL,            -- 'anomaly' | 'predict'
  first_flag_ts TEXT NOT NULL,
  last_flag_ts  TEXT,
  severity      REAL,
  interpretation TEXT,
  status        TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS ix_alerts_asset ON alerts(asset_id, first_flag_ts);

-- Operator playbook (Part E), one row per failure mode section.
CREATE TABLE IF NOT EXISTS playbook (
  mode     TEXT NOT NULL,
  section  TEXT NOT NULL,                 -- symptoms | checks | actions | spares | lead_time
  body     TEXT NOT NULL,
  PRIMARY KEY (mode, section)
);
