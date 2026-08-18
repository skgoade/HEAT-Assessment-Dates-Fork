-- Training Focus card on PDF page 1 (next to Mechanical Observation).
ALTER TABLE PlayerDev.hitting_assessments
    ADD COLUMN training_focus TEXT NULL;
