# Schema Customization Guide

This guide explains how to customize the database schema to match your specific Blast Motion and HitTrax table structures.

## 🎯 Overview

The hitting assessment system links assessment dates with your existing swing data tables. The provided schema makes assumptions about your table and column names. You'll need to customize the views to match your actual database structure.

## 📋 Required Information

Before customizing, gather this information about your database:

### Blast Motion Table
- **Table name**: ____________________
- **Player name column**: ____________________
- **Date column**: ____________________
- **Timestamp column** (for matching with HitTrax): ____________________
- **Metric columns**: List all columns you want in reports

### HitTrax Table
- **Table name**: ____________________
- **Player name column**: ____________________
- **Date column**: ____________________
- **Timestamp column** (for matching with Blast): ____________________
- **Metric columns**: List all columns you want in reports

## 🔧 Step-by-Step Customization

### Step 1: Identify Your Table Names

Open `deployment/schema.sql` and find these references:

**Default names used in schema:**
- `blast_swing_data` - Replace with your Blast table name
- `hittrax_swing_data` - Replace with your HitTrax table name

**Find and replace:**
```sql
-- Change this:
FROM blast_swing_data bsd

-- To this (example):
FROM blast_metrics bsd

-- And this:
FROM hittrax_swing_data hsd

-- To this (example):
FROM hittrax_results hsd
```

### Step 2: Update Column Names

#### Player Name Column

**Default:** `player_name`

If your column is different (e.g., `athlete_name`, `player`, `name`):

```sql
-- Change all instances of:
ON ha.player_name = bsd.player_name

-- To:
ON ha.player_name = bsd.athlete_name  -- or your column name
```

#### Date Columns

**Default:** `session_date`

```sql
-- Change:
bsd.session_date

-- To your column name, e.g.:
bsd.workout_date
bsd.test_date
bsd.date
```

#### Timestamp Columns

**Default:** `ts`

For the combined view that joins Blast and HitTrax:

```sql
-- Change:
ON bsd.ts = hsd.ts

-- To:
ON bsd.timestamp = hsd.timestamp  -- or your column names
```

### Step 3: Update Metric Columns

#### Blast Metrics

**Default columns in assessment_blast_view:**
```sql
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
bsd.swing_count
```

**Example customization:**

If your Blast table has different column names:
```sql
-- Original
bsd.bat_speed,
bsd.attack_angle,

-- Your columns might be:
bsd.bat_speed_mph,
bsd.attack_angle_deg,
bsd.swing_speed,  -- new column
bsd.efficiency,   -- new column
```

#### HitTrax Metrics

**Default columns in assessment_hittrax_view:**
```sql
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
hsd.barrel_percentage
```

**Add or remove columns** based on what's in your HitTrax table.

### Step 4: Customize Date Ranges

**Default:** ±7 days from assessment date

To change the linking window:

```sql
-- Current:
AND ABS(DATEDIFF(bsd.session_date, ha.assessment_date)) <= 7

-- For ±3 days:
AND ABS(DATEDIFF(bsd.session_date, ha.assessment_date)) <= 3

-- For ±14 days:
AND ABS(DATEDIFF(bsd.session_date, ha.assessment_date)) <= 14

-- For only AFTER assessment (next 7 days):
AND DATEDIFF(bsd.session_date, ha.assessment_date) BETWEEN 0 AND 7

-- For only BEFORE assessment (previous 7 days):
AND DATEDIFF(bsd.session_date, ha.assessment_date) BETWEEN -7 AND 0
```

## 📝 Example Customization

Here's a complete example of customizing the Blast view:

### Original View
```sql
CREATE OR REPLACE VIEW assessment_blast_view AS
SELECT 
    ha.assessment_id,
    ha.assessment_date,
    ha.player_name,
    bsd.session_date as blast_session_date,
    bsd.bat_speed,
    bsd.attack_angle,
    DATEDIFF(bsd.session_date, ha.assessment_date) as days_from_assessment
FROM hitting_assessments ha
LEFT JOIN blast_swing_data bsd 
    ON ha.player_name = bsd.player_name
    AND ABS(DATEDIFF(bsd.session_date, ha.assessment_date)) <= 7;
```

### Customized View
```sql
CREATE OR REPLACE VIEW assessment_blast_view AS
SELECT 
    ha.assessment_id,
    ha.assessment_date,
    ha.player_name,
    -- Changed: table is 'blast_metrics', date column is 'test_date'
    bm.test_date as blast_session_date,
    -- Changed: column names
    bm.swing_speed_mph as bat_speed,
    bm.vertical_angle as attack_angle,
    -- Added: new metrics from your table
    bm.hand_speed,
    bm.rotational_accel,
    DATEDIFF(bm.test_date, ha.assessment_date) as days_from_assessment
FROM hitting_assessments ha
-- Changed: table name and alias
LEFT JOIN blast_metrics bm 
    -- Changed: your player column is 'athlete_name'
    ON ha.player_name = bm.athlete_name
    -- Changed: using your date column
    AND ABS(DATEDIFF(bm.test_date, ha.assessment_date)) <= 7;
```

## 🧪 Testing Your Changes

After customizing the schema, test each view:

### Test 1: Check View Creation
```sql
-- Show all views
SHOW FULL TABLES WHERE table_type = 'VIEW';

-- Should show:
-- assessment_blast_view
-- assessment_hittrax_view
-- assessment_combined_view
-- assessment_summary
-- player_assessment_history
```

### Test 2: Query Each View
```sql
-- Test Blast view
SELECT * FROM assessment_blast_view LIMIT 5;

-- Test HitTrax view
SELECT * FROM assessment_hittrax_view LIMIT 5;

-- Test combined view
SELECT * FROM assessment_combined_view LIMIT 5;
```

### Test 3: Check Data Linking
```sql
-- Create a test assessment
INSERT INTO hitting_assessments (assessment_date, player_name)
VALUES (CURDATE(), 'Test Player');

-- Check if it links with your data
SELECT * FROM assessment_blast_view 
WHERE player_name = 'Test Player' 
ORDER BY assessment_id DESC 
LIMIT 1;
```

## 🚨 Common Issues

### Issue 1: "Unknown column" error
**Problem:** Column name doesn't exist in your table

**Solution:** Check your actual column names:
```sql
DESCRIBE blast_swing_data;  -- or your table name
```

### Issue 2: View returns no data
**Possible causes:**
1. Player names don't match exactly (check for extra spaces, capitalization)
2. No swing data exists in the date range
3. Date column is wrong type or NULL

**Debug:**
```sql
-- Check player names in both tables
SELECT DISTINCT player_name FROM hitting_assessments;
SELECT DISTINCT player_name FROM blast_swing_data;  -- use your table name

-- Check dates
SELECT player_name, session_date 
FROM blast_swing_data 
WHERE player_name = 'John Smith'
ORDER BY session_date DESC LIMIT 10;
```

### Issue 3: Timestamp join returns no matches
**Problem:** Blast and HitTrax timestamps don't match exactly

**Solutions:**

Option A: Match by date and swing sequence instead of timestamp:
```sql
LEFT JOIN hittrax_swing_data hsd 
    ON bsd.player_name = hsd.player_name
    AND bsd.session_date = hsd.session_date
    AND bsd.swing_number = hsd.swing_number
```

Option B: Use time window matching:
```sql
LEFT JOIN hittrax_swing_data hsd 
    ON bsd.player_name = hsd.player_name
    AND ABS(TIMESTAMPDIFF(SECOND, bsd.timestamp, hsd.timestamp)) <= 2
```

## 📋 Customization Checklist

Use this checklist when modifying the schema:

- [ ] Identified Blast table name
- [ ] Identified HitTrax table name
- [ ] Found player name columns (both tables)
- [ ] Found date columns (both tables)
- [ ] Found timestamp columns (if using combined view)
- [ ] Listed all desired metric columns
- [ ] Updated all three main views (Blast, HitTrax, Combined)
- [ ] Tested view creation (no errors)
- [ ] Created test assessment
- [ ] Verified data linking works
- [ ] Checked that metrics appear correctly

## 💡 Advanced Customizations

### Add Calculated Metrics

```sql
-- Add swing quality score
(bsd.bat_speed * 0.4 + bsd.attack_angle * 0.3 + bsd.power * 0.3) as swing_quality_score,

-- Add exit velo efficiency
(hsd.exit_velocity / bsd.bat_speed) as exit_velo_efficiency,
```

### Add Filters

```sql
-- Only include quality swings
WHERE bsd.bat_speed >= 50 
  AND hsd.exit_velocity >= 70

-- Exclude specific swing types
WHERE hsd.result != 'Miss'
```

### Add Aggregations

Create a new view with aggregated metrics:

```sql
CREATE OR REPLACE VIEW assessment_averages AS
SELECT 
    ha.assessment_id,
    ha.player_name,
    ha.assessment_date,
    COUNT(DISTINCT bsd.session_date) as blast_sessions,
    AVG(bsd.bat_speed) as avg_bat_speed,
    MAX(bsd.bat_speed) as max_bat_speed,
    AVG(hsd.exit_velocity) as avg_exit_velo,
    MAX(hsd.exit_velocity) as max_exit_velo
FROM hitting_assessments ha
LEFT JOIN blast_swing_data bsd ON ...
LEFT JOIN hittrax_swing_data hsd ON ...
GROUP BY ha.assessment_id, ha.player_name, ha.assessment_date;
```

## 🎓 Need More Help?

1. **Review your table structure:**
   ```sql
   DESCRIBE your_blast_table;
   DESCRIBE your_hittrax_table;
   ```

2. **Check sample data:**
   ```sql
   SELECT * FROM your_blast_table LIMIT 1;
   SELECT * FROM your_hittrax_table LIMIT 1;
   ```

3. **Test joins manually:**
   ```sql
   SELECT *
   FROM hitting_assessments ha
   LEFT JOIN your_blast_table bsd 
       ON ha.player_name = bsd.your_player_column
   LIMIT 5;
   ```

Remember: The views are just SELECT statements. You can always drop and recreate them:

```sql
DROP VIEW IF EXISTS assessment_blast_view;
-- Then create again with your customizations
```
