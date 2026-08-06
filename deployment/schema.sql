-- RBI Hitting Assessment Database Schema
-- Run this script to create the hitting_assessments table and views

-- Main assessment tracking table
CREATE TABLE IF NOT EXISTS hitting_assessments (
    assessment_id INT AUTO_INCREMENT PRIMARY KEY,
    assessment_date DATE NOT NULL,
    player_name VARCHAR(255) NOT NULL,
    trainer_name VARCHAR(255),
    notes TEXT,
    video_analysis_url VARCHAR(1024) NULL,
    used_blast TINYINT(1) NOT NULL DEFAULT 1,
    used_hittrax TINYINT(1) NOT NULL DEFAULT 1,
    used_vald TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    -- M1: type + comparison links for static-site PDF flow
    assessment_type ENUM('initial', 'retest') NOT NULL DEFAULT 'initial',
    -- baseline is resolved at report time (earliest initial); not stored
    previous_assessment_id INT NULL,
    report_gcs_uri VARCHAR(1024) NULL,
    INDEX idx_player_name (player_name),
    INDEX idx_assessment_date (assessment_date),
    INDEX idx_player_date (player_name, assessment_date),
    INDEX idx_ha_player_type (player_name, assessment_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- View 1: Hitting Assessments joined with Blast Swing Data
-- Assumes blast_swing_data table exists with columns: player_name, session_date, and various metrics
-- Adjust column names based on your actual Blast table structure
CREATE OR REPLACE VIEW assessment_blast_view AS
SELECT 
    ha.assessment_id,
    ha.assessment_date,
    ha.player_name,
    ha.trainer_name,
    ha.notes,
    ha.created_at,
    -- Blast swing data columns (adjust these to match your actual Blast table)
    bsd.session_date as blast_session_date,
    bsd.bat_speed,
    bsd.peak_hand_speed,
    bsd.time_to_contact,
    bsd.attack_angle,
    bsd.on_plane_efficiency,
    bsd.rotation_acceleration,
    bsd.swing_length,
    bsd.early_connection,
    bsd.connection_at_impact,
    bsd.vertical_bat_angle,
    bsd.power,
    bsd.swing_count,
    -- Calculate days between assessment and blast session
    DATEDIFF(bsd.session_date, ha.assessment_date) as days_from_assessment
FROM hitting_assessments ha
LEFT JOIN blast_swing_data bsd 
    ON ha.player_name = bsd.player_name
    AND ABS(DATEDIFF(bsd.session_date, ha.assessment_date)) <= 7  -- Within 7 days of assessment
ORDER BY ha.assessment_date DESC, ha.player_name, bsd.session_date;

-- View 2: Hitting Assessments joined with HitTrax Swing Data
-- Assumes hittrax_swing_data table exists with columns: player_name, session_date, and various metrics
-- Adjust column names based on your actual HitTrax table structure
CREATE OR REPLACE VIEW assessment_hittrax_view AS
SELECT 
    ha.assessment_id,
    ha.assessment_date,
    ha.player_name,
    ha.trainer_name,
    ha.notes,
    ha.created_at,
    -- HitTrax swing data columns (adjust these to match your actual HitTrax table)
    hsd.session_date as hittrax_session_date,
    hsd.exit_velocity,
    hsd.launch_angle,
    hsd.distance,
    hsd.spray_direction,
    hsd.hit_type,
    hsd.result,
    hsd.pitch_speed,
    hsd.pitch_type,
    hsd.contact_point_x,
    hsd.contact_point_y,
    hsd.contact_point_z,
    hsd.swing_count,
    hsd.hard_hit_percentage,
    hsd.barrel_percentage,
    -- Calculate days between assessment and hittrax session
    DATEDIFF(hsd.session_date, ha.assessment_date) as days_from_assessment
FROM hitting_assessments ha
LEFT JOIN hittrax_swing_data hsd 
    ON ha.player_name = hsd.player_name
    AND ABS(DATEDIFF(hsd.session_date, ha.assessment_date)) <= 7  -- Within 7 days of assessment
ORDER BY ha.assessment_date DESC, ha.player_name, hsd.session_date;

-- View 3: Hitting Assessments with Combined Blast and HitTrax Data
-- This view joins Blast and HitTrax on a common timestamp field, then joins with assessments
-- Assumes both tables have a 'ts' (timestamp) field for matching swings
CREATE OR REPLACE VIEW assessment_combined_view AS
SELECT 
    ha.assessment_id,
    ha.assessment_date,
    ha.player_name,
    ha.trainer_name,
    ha.notes,
    ha.created_at,
    -- Common session info
    COALESCE(bsd.session_date, hsd.session_date) as session_date,
    bsd.ts as blast_timestamp,
    hsd.ts as hittrax_timestamp,
    -- Blast metrics
    bsd.bat_speed,
    bsd.peak_hand_speed,
    bsd.time_to_contact,
    bsd.attack_angle as blast_attack_angle,
    bsd.on_plane_efficiency,
    bsd.rotation_acceleration,
    bsd.swing_length,
    bsd.early_connection,
    bsd.connection_at_impact,
    bsd.vertical_bat_angle,
    bsd.power as blast_power,
    -- HitTrax metrics
    hsd.exit_velocity,
    hsd.launch_angle,
    hsd.distance,
    hsd.spray_direction,
    hsd.hit_type,
    hsd.result,
    hsd.pitch_speed,
    hsd.pitch_type,
    hsd.contact_point_x,
    hsd.contact_point_y,
    hsd.contact_point_z,
    -- Calculated fields
    DATEDIFF(COALESCE(bsd.session_date, hsd.session_date), ha.assessment_date) as days_from_assessment,
    -- Quality metrics combining both systems
    CASE 
        WHEN hsd.exit_velocity >= 95 AND bsd.bat_speed >= 70 THEN 'Elite'
        WHEN hsd.exit_velocity >= 90 AND bsd.bat_speed >= 65 THEN 'Above Average'
        WHEN hsd.exit_velocity >= 85 AND bsd.bat_speed >= 60 THEN 'Average'
        ELSE 'Below Average'
    END as swing_quality_tier
FROM hitting_assessments ha
LEFT JOIN blast_swing_data bsd 
    ON ha.player_name = bsd.player_name
    AND ABS(DATEDIFF(bsd.session_date, ha.assessment_date)) <= 7
LEFT JOIN hittrax_swing_data hsd 
    ON bsd.ts = hsd.ts  -- Join Blast and HitTrax on timestamp
    AND bsd.player_name = hsd.player_name
WHERE bsd.ts IS NOT NULL OR hsd.ts IS NOT NULL  -- Only include rows with actual swing data
ORDER BY ha.assessment_date DESC, ha.player_name, COALESCE(bsd.session_date, hsd.session_date);

-- Helper view: Assessment summary with swing counts
CREATE OR REPLACE VIEW assessment_summary AS
SELECT 
    ha.assessment_id,
    ha.assessment_date,
    ha.player_name,
    ha.trainer_name,
    ha.notes,
    -- Count of associated swing data within 7 days
    COUNT(DISTINCT bsd.session_date) as blast_sessions,
    COUNT(DISTINCT hsd.session_date) as hittrax_sessions,
    -- First and last session dates
    MIN(bsd.session_date) as first_blast_session,
    MAX(bsd.session_date) as last_blast_session,
    MIN(hsd.session_date) as first_hittrax_session,
    MAX(hsd.session_date) as last_hittrax_session,
    ha.created_at
FROM hitting_assessments ha
LEFT JOIN blast_swing_data bsd 
    ON ha.player_name = bsd.player_name
    AND ABS(DATEDIFF(bsd.session_date, ha.assessment_date)) <= 7
LEFT JOIN hittrax_swing_data hsd 
    ON ha.player_name = hsd.player_name
    AND ABS(DATEDIFF(hsd.session_date, ha.assessment_date)) <= 7
GROUP BY ha.assessment_id, ha.assessment_date, ha.player_name, ha.trainer_name, ha.notes, ha.created_at
ORDER BY ha.assessment_date DESC;

-- Helper view: Player assessment history for progression tracking
CREATE OR REPLACE VIEW player_assessment_history AS
SELECT 
    assessment_id,
    assessment_date,
    player_name,
    trainer_name,
    notes,
    created_at,
    -- Row number to identify which assessment (1st, 2nd, 3rd, etc.)
    ROW_NUMBER() OVER (PARTITION BY player_name ORDER BY assessment_date) as assessment_number,
    -- Days since previous assessment
    DATEDIFF(
        assessment_date, 
        LAG(assessment_date) OVER (PARTITION BY player_name ORDER BY assessment_date)
    ) as days_since_last_assessment,
    -- First and most recent assessment dates for the player
    FIRST_VALUE(assessment_date) OVER (PARTITION BY player_name ORDER BY assessment_date) as first_assessment_date,
    FIRST_VALUE(assessment_date) OVER (PARTITION BY player_name ORDER BY assessment_date DESC) as most_recent_assessment_date
FROM hitting_assessments
ORDER BY player_name, assessment_date;

-- Example query to compare 4th assessment to last and first assessments:
-- This can be used in your automated reports
/*
SELECT 
    current.assessment_id,
    current.player_name,
    current.assessment_date as current_date,
    current.assessment_number,
    
    -- First assessment data
    first.assessment_date as first_assessment_date,
    first.notes as first_notes,
    
    -- Previous assessment data
    prev.assessment_date as previous_assessment_date,
    prev.notes as previous_notes,
    
    -- Current assessment data
    current.notes as current_notes,
    
    -- Time progressions
    DATEDIFF(current.assessment_date, first.assessment_date) as days_since_first,
    current.days_since_last_assessment
FROM player_assessment_history current
LEFT JOIN player_assessment_history first 
    ON current.player_name = first.player_name 
    AND first.assessment_number = 1
LEFT JOIN player_assessment_history prev 
    ON current.player_name = prev.player_name 
    AND prev.assessment_number = current.assessment_number - 1
WHERE current.assessment_number = 4;  -- For 4th assessment specifically
*/
