-- One-shot schema checks for HEAT assessment PDF features.
-- Run against PlayerDev after applying:
--   deployment/assessment_attachments.sql
--   deployment/assessment_report_versions.sql
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
