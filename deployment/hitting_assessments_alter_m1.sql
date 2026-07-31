-- M1 alter for existing PlayerDev.hitting_assessments
-- Skip any ADD COLUMN that already exists (your DESCRIBE shows these are present).

ALTER TABLE PlayerDev.hitting_assessments
  ADD COLUMN assessment_type ENUM('initial', 'retest') NOT NULL DEFAULT 'initial',
  ADD COLUMN baseline_assessment_id INT NULL,
  ADD COLUMN previous_assessment_id INT NULL,
  ADD COLUMN report_gcs_uri VARCHAR(1024) NULL;

-- Optional index (run once if missing)
-- CREATE INDEX idx_ha_player_type ON PlayerDev.hitting_assessments (player_name, assessment_type);
