-- Read routes for the Part E assistant: predictions and alerts per asset, by time.
CREATE INDEX IF NOT EXISTS ix_predictions_asset ON predictions(asset_id, as_of);
