-- Athlete bio for the HEAT PDF header (join by player name at report time).
-- Probe first — this table’s DDL is not in the HEAT repo:
--   SHOW TABLES FROM PlayerDev LIKE 'player_directory';
--   SHOW COLUMNS FROM PlayerDev.player_directory;
-- Skip this ALTER if height / weight already exist (any name; the PDF lookup
-- matches common aliases). Add VARCHAR columns if they are missing.
ALTER TABLE PlayerDev.player_directory
    ADD COLUMN height VARCHAR(32) NULL,
    ADD COLUMN weight VARCHAR(32) NULL;
