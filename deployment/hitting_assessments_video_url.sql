-- Optional video analysis URL for assessment reports.
ALTER TABLE PlayerDev.hitting_assessments
    ADD COLUMN video_analysis_url VARCHAR(1024) NULL;
