-- PDF report version history for HEAT assessments.
-- Run on PlayerDev (Cloud SQL) once. Does not delete or rewrite existing PDFs.

CREATE TABLE IF NOT EXISTS assessment_report_versions (
    version_id      INT AUTO_INCREMENT PRIMARY KEY,
    assessment_id   INT NOT NULL,
    version_num     INT NOT NULL,
    gcs_uri         VARCHAR(1024) NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_arv_assessment_version (assessment_id, version_num),
    INDEX idx_arv_assessment (assessment_id),
    CONSTRAINT fk_arv_assessment
        FOREIGN KEY (assessment_id) REFERENCES hitting_assessments (assessment_id)
        ON DELETE CASCADE
);
