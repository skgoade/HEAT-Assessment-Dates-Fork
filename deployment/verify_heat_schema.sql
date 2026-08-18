-- One-shot schema checks for HEAT assessment PDF features.
-- Run against PlayerDev after applying:
--   deployment/assessment_attachments.sql
--   deployment/assessment_report_versions.sql
--   deployment/hitting_assessments_mechanics_phase_notes.sql
--   deployment/hitting_assessments_pdf_summaries.sql
--   deployment/player_directory_height_weight.sql
--   (plus earlier alters: tool flags, video_url, M1 columns if not already present)

-- Expect 1 row each:
SHOW TABLES LIKE 'assessment_attachments';
SHOW TABLES LIKE 'assessment_report_versions';

-- Expect columns used by the API / PDF pipeline:
SHOW COLUMNS FROM hitting_assessments LIKE 'report_gcs_uri';
SHOW COLUMNS FROM hitting_assessments LIKE 'used_blast';
SHOW COLUMNS FROM hitting_assessments LIKE 'used_hittrax';
SHOW COLUMNS FROM hitting_assessments LIKE 'used_vald';
SHOW COLUMNS FROM hitting_assessments LIKE 'video_analysis_url';
SHOW COLUMNS FROM hitting_assessments LIKE 'previous_assessment_id';
SHOW COLUMNS FROM hitting_assessments LIKE 'assessment_type';
SHOW COLUMNS FROM hitting_assessments LIKE 'mechanics_phase_notes';
SHOW COLUMNS FROM hitting_assessments LIKE 'mechanical_summary';
SHOW COLUMNS FROM hitting_assessments LIKE 'best_of_day_summary';
SHOW COLUMNS FROM hitting_assessments LIKE 'training_focus';

-- Athlete bio for PDF header (probe names; height/weight may be new):
SHOW TABLES LIKE 'player_directory';
SHOW COLUMNS FROM player_directory LIKE 'date_of_birth';
SHOW COLUMNS FROM player_directory LIKE 'height';
SHOW COLUMNS FROM player_directory LIKE 'weight';
