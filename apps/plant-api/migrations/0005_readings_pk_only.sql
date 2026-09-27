-- Full plant (82 tags): every row written also wrote this index, and D1 counts index
-- writes as rows written. Without it a sim hour costs 82 rows instead of 164, which keeps
-- ingest inside the free 100k rows/day at clock speed 30 (docs/design/full-plant.md).
-- Ingest now finds hours through the primary key (tag, ts) on the sentinel tag PLANT.LOAD.
DROP INDEX IF EXISTS ix_readings_ts;
