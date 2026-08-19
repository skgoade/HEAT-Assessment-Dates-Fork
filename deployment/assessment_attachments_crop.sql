-- Pan/zoom crop box for phase photos (normalized x,y,w,h JSON).
-- Skip if crop_json already exists:
--   SHOW COLUMNS FROM PlayerDev.assessment_attachments LIKE 'crop_json';
ALTER TABLE PlayerDev.assessment_attachments
    ADD COLUMN crop_json VARCHAR(255) NULL;
