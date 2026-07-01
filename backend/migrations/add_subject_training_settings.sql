-- Migration: Add per-subject LoRA training hyperparameters
-- Date: 2026-06-29
-- Description: Stores resolution/rank/alpha/learning_rate/steps for Cast Studio training

ALTER TABLE subjects ADD COLUMN IF NOT EXISTS training_settings_json JSON;

SELECT 'Migration completed: subjects.training_settings_json added' AS status;