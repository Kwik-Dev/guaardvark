-- Migration: Promote generated samples into Training Data after train success
-- Date: 2026-07-23
-- Description: subject_samples.promoted_to_training / promoted_at so the Generate
-- Character sheet can hide samples once they graduate into the durable training set.

ALTER TABLE subject_samples ADD COLUMN IF NOT EXISTS promoted_to_training BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE subject_samples ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS ix_subject_samples_promoted_to_training
  ON subject_samples (promoted_to_training);

SELECT 'Migration completed: subject_samples promotion columns added' AS status;
