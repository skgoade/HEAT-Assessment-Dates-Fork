-- Trainer visual context attachments for HEAT assessment PDFs.
-- Run on PlayerDev (Cloud SQL) once before using upload endpoints.

CREATE TABLE IF NOT EXISTS assessment_attachments (
    attachment_id   INT AUTO_INCREMENT PRIMARY KEY,
    assessment_id   INT NOT NULL,
    slot            VARCHAR(64) NOT NULL DEFAULT 'other',
    caption         VARCHAR(512) NULL,
    gcs_uri         VARCHAR(1024) NOT NULL,
    local_path      VARCHAR(1024) NULL,
    content_type    VARCHAR(128) NULL,
    sort_order      INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_aa_assessment (assessment_id),
    CONSTRAINT fk_aa_assessment
        FOREIGN KEY (assessment_id) REFERENCES hitting_assessments (assessment_id)
        ON DELETE CASCADE
);
