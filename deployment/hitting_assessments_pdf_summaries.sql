-- Full-width PDF text boxes (page 1 Mechanical Summary; Best of Day overall notes).
-- Apply on PlayerDev before create/regen that persist these fields.
ALTER TABLE PlayerDev.hitting_assessments
    ADD COLUMN mechanical_summary TEXT NULL,
    ADD COLUMN best_of_day_summary TEXT NULL;
