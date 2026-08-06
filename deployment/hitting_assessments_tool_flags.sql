-- Tool-selection flags for conditional assessment report sections.
-- Defaults preserve the existing behavior for historical assessments.
ALTER TABLE PlayerDev.hitting_assessments
    ADD COLUMN used_blast TINYINT(1) NOT NULL DEFAULT 1,
    ADD COLUMN used_hittrax TINYINT(1) NOT NULL DEFAULT 1,
    ADD COLUMN used_vald TINYINT(1) NOT NULL DEFAULT 1;
