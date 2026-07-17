-- Migration: Add Cast subject training failure + related columns
-- Date: 2026-07-16
-- Description: Idempotent ALTERs for columns that models.py expects but older
--              stamped/client DBs may lack. Prefer app.py reconcile + schema_sync
--              on boot; this file is for manual repair / documentation.

ALTER TABLE subjects ADD COLUMN IF NOT EXISTS training_error TEXT;
ALTER TABLE subjects ADD COLUMN IF NOT EXISTS current_training_job_id VARCHAR(64);
ALTER TABLE subjects ADD COLUMN IF NOT EXISTS last_trained_image_paths JSON NOT NULL DEFAULT '[]'::json;
ALTER TABLE subjects ADD COLUMN IF NOT EXISTS last_trained_at TIMESTAMP;
ALTER TABLE subjects ADD COLUMN IF NOT EXISTS bible TEXT;
ALTER TABLE subjects ADD COLUMN IF NOT EXISTS training_settings_json JSON;

SELECT 'Migration completed: subjects training_error (+ related cast columns) ensured' AS status;
