-- Per-swing-phase coach notes for mechanics PDF cards (JSON object keyed by slot).
-- Example: {"load_phase": "Early load looks soft", "impact": "Good extension"}
ALTER TABLE PlayerDev.hitting_assessments
    ADD COLUMN mechanics_phase_notes JSON NULL;
