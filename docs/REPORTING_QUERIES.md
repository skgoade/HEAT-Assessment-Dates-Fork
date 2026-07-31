# Building Automated Reports - SQL Query Examples

This guide provides SQL query examples for creating automated reports that compare assessments and track player progress.

## 🎯 Common Reporting Scenarios

### Scenario 1: Compare 4th Assessment to 1st Assessment

This query compares a player's 4th assessment to their initial baseline.

```sql
SELECT 
    -- Current (4th) Assessment
    current.assessment_id as current_assessment_id,
    current.player_name,
    current.assessment_date as current_date,
    current.assessment_number,
    current.notes as current_notes,
    
    -- First Assessment
    first.assessment_date as first_assessment_date,
    first.notes as first_notes,
    
    -- Time Between
    DATEDIFF(current.assessment_date, first.assessment_date) as days_since_first,
    
    -- Blast Metrics Comparison (averages on each assessment calendar day)
    AVG(CASE WHEN b_current.assessment_id = current.assessment_id 
        THEN b_current.bat_speed END) as current_bat_speed,
    AVG(CASE WHEN b_first.assessment_id = first.assessment_id 
        THEN b_first.bat_speed END) as first_bat_speed,
    AVG(CASE WHEN b_current.assessment_id = current.assessment_id 
        THEN b_current.attack_angle END) as current_attack_angle,
    AVG(CASE WHEN b_first.assessment_id = first.assessment_id 
        THEN b_first.attack_angle END) as first_attack_angle,
    
    -- HitTrax Metrics Comparison
    AVG(CASE WHEN h_current.assessment_id = current.assessment_id 
        THEN h_current.exit_velocity END) as current_exit_velo,
    AVG(CASE WHEN h_first.assessment_id = first.assessment_id 
        THEN h_first.exit_velocity END) as first_exit_velo,
    AVG(CASE WHEN h_current.assessment_id = current.assessment_id 
        THEN h_current.launch_angle END) as current_launch_angle,
    AVG(CASE WHEN h_first.assessment_id = first.assessment_id 
        THEN h_first.launch_angle END) as first_launch_angle,
    
    -- Calculate Improvements
    AVG(CASE WHEN b_current.assessment_id = current.assessment_id 
        THEN b_current.bat_speed END) - 
    AVG(CASE WHEN b_first.assessment_id = first.assessment_id 
        THEN b_first.bat_speed END) as bat_speed_improvement,
    
    AVG(CASE WHEN h_current.assessment_id = current.assessment_id 
        THEN h_current.exit_velocity END) - 
    AVG(CASE WHEN h_first.assessment_id = first.assessment_id 
        THEN h_first.exit_velocity END) as exit_velo_improvement

FROM player_assessment_history current

-- Join with first assessment
INNER JOIN player_assessment_history first 
    ON current.player_name = first.player_name 
    AND first.assessment_number = 1

-- Join with current assessment's Blast data
LEFT JOIN assessment_blast_view b_current 
    ON current.assessment_id = b_current.assessment_id

-- Join with first assessment's Blast data
LEFT JOIN assessment_blast_view b_first 
    ON first.assessment_id = b_first.assessment_id

-- Join with current assessment's HitTrax data
LEFT JOIN assessment_hittrax_view h_current 
    ON current.assessment_id = h_current.assessment_id

-- Join with first assessment's HitTrax data
LEFT JOIN assessment_hittrax_view h_first 
    ON first.assessment_id = h_first.assessment_id

WHERE current.assessment_number = 4  -- Looking at 4th assessments only

GROUP BY 
    current.assessment_id,
    current.player_name,
    current.assessment_date,
    current.assessment_number,
    current.notes,
    first.assessment_date,
    first.notes;
```

### Scenario 2: Compare Most Recent Assessment to Previous

Track session-over-session progress.

```sql
SELECT 
    -- Current Assessment
    current.assessment_id,
    current.player_name,
    current.assessment_date as current_date,
    current.assessment_number,
    current.notes as current_notes,
    
    -- Previous Assessment
    prev.assessment_date as previous_date,
    prev.assessment_number as previous_number,
    prev.notes as previous_notes,
    current.days_since_last_assessment,
    
    -- Blast Metrics
    AVG(b_current.bat_speed) as current_bat_speed,
    AVG(b_prev.bat_speed) as previous_bat_speed,
    AVG(b_current.attack_angle) as current_attack_angle,
    AVG(b_prev.attack_angle) as previous_attack_angle,
    AVG(b_current.power) as current_power,
    AVG(b_prev.power) as previous_power,
    
    -- HitTrax Metrics
    AVG(h_current.exit_velocity) as current_exit_velo,
    AVG(h_prev.exit_velocity) as previous_exit_velo,
    AVG(h_current.distance) as current_distance,
    AVG(h_prev.distance) as previous_distance,
    
    -- Changes
    AVG(b_current.bat_speed) - AVG(b_prev.bat_speed) as bat_speed_change,
    AVG(h_current.exit_velocity) - AVG(h_prev.exit_velocity) as exit_velo_change

FROM player_assessment_history current

-- Join with previous assessment
LEFT JOIN player_assessment_history prev 
    ON current.player_name = prev.player_name 
    AND prev.assessment_number = current.assessment_number - 1

-- Blast data for both assessments
LEFT JOIN assessment_blast_view b_current 
    ON current.assessment_id = b_current.assessment_id
LEFT JOIN assessment_blast_view b_prev 
    ON prev.assessment_id = b_prev.assessment_id

-- HitTrax data for both assessments
LEFT JOIN assessment_hittrax_view h_current 
    ON current.assessment_id = h_current.assessment_id
LEFT JOIN assessment_hittrax_view h_prev 
    ON prev.assessment_id = h_prev.assessment_id

WHERE current.assessment_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)  -- Last 30 days

GROUP BY 
    current.assessment_id,
    current.player_name,
    current.assessment_date,
    current.assessment_number,
    current.notes,
    prev.assessment_date,
    prev.assessment_number,
    prev.notes,
    current.days_since_last_assessment

ORDER BY current.assessment_date DESC;
```

### Scenario 3: Player Progress Report (All Assessments)

Complete timeline of a player's assessments with metrics.

```sql
SELECT 
    ha.assessment_number,
    ha.assessment_date,
    ha.trainer_name,
    ha.notes,
    ha.days_since_last_assessment,
    
    -- Blast Metrics (Averages)
    COUNT(DISTINCT bv.blast_session_date) as blast_sessions,
    ROUND(AVG(bv.bat_speed), 1) as avg_bat_speed,
    ROUND(MAX(bv.bat_speed), 1) as max_bat_speed,
    ROUND(AVG(bv.attack_angle), 1) as avg_attack_angle,
    ROUND(AVG(bv.power), 1) as avg_power,
    
    -- HitTrax Metrics (Averages)
    COUNT(DISTINCT hv.hittrax_session_date) as hittrax_sessions,
    ROUND(AVG(hv.exit_velocity), 1) as avg_exit_velo,
    ROUND(MAX(hv.exit_velocity), 1) as max_exit_velo,
    ROUND(AVG(hv.launch_angle), 1) as avg_launch_angle,
    ROUND(AVG(hv.distance), 1) as avg_distance,
    
    -- Trend from previous assessment
    ROUND(
        AVG(bv.bat_speed) - LAG(AVG(bv.bat_speed)) 
        OVER (PARTITION BY ha.player_name ORDER BY ha.assessment_date),
    1) as bat_speed_trend,
    
    ROUND(
        AVG(hv.exit_velocity) - LAG(AVG(hv.exit_velocity)) 
        OVER (PARTITION BY ha.player_name ORDER BY ha.assessment_date),
    1) as exit_velo_trend

FROM player_assessment_history ha

LEFT JOIN assessment_blast_view bv 
    ON ha.assessment_id = bv.assessment_id

LEFT JOIN assessment_hittrax_view hv 
    ON ha.assessment_id = hv.assessment_id

WHERE ha.player_name = 'John Smith'  -- Replace with actual player name

GROUP BY 
    ha.assessment_id,
    ha.assessment_number,
    ha.assessment_date,
    ha.trainer_name,
    ha.notes,
    ha.days_since_last_assessment,
    ha.player_name

ORDER BY ha.assessment_date;
```

### Scenario 4: Team Summary Report

Overview of all recent assessments across the team.

```sql
SELECT 
    ha.player_name,
    MAX(ha.assessment_date) as last_assessment_date,
    MAX(ha.assessment_number) as total_assessments,
    MAX(ha.trainer_name) as last_trainer,
    
    -- Recent performance (from last assessment)
    (SELECT AVG(bat_speed)
     FROM assessment_blast_view
     WHERE assessment_id = (
         SELECT assessment_id 
         FROM hitting_assessments ha2 
         WHERE ha2.player_name = ha.player_name 
         ORDER BY assessment_date DESC LIMIT 1
     )
    ) as recent_bat_speed,
    
    (SELECT AVG(exit_velocity)
     FROM assessment_hittrax_view
     WHERE assessment_id = (
         SELECT assessment_id 
         FROM hitting_assessments ha2 
         WHERE ha2.player_name = ha.player_name 
         ORDER BY assessment_date DESC LIMIT 1
     )
    ) as recent_exit_velo,
    
    -- Days since last assessment
    DATEDIFF(CURDATE(), MAX(ha.assessment_date)) as days_since_last

FROM player_assessment_history ha

GROUP BY ha.player_name

ORDER BY MAX(ha.assessment_date) DESC;
```

### Scenario 5: Assessment with Swing-by-Swing Detail

Get individual swing details for a specific assessment.

```sql
-- Blast swings
SELECT 
    'Blast' as data_source,
    bv.blast_session_date as session_date,
    bv.bat_speed,
    bv.attack_angle,
    bv.power,
    bv.swing_length,
    bv.on_plane_efficiency,
    bv.days_from_assessment
FROM assessment_blast_view bv
WHERE bv.assessment_id = 42  -- Replace with actual assessment_id
ORDER BY bv.blast_session_date, bv.blast_timestamp;

-- HitTrax swings
SELECT 
    'HitTrax' as data_source,
    hv.hittrax_session_date as session_date,
    hv.exit_velocity,
    hv.launch_angle,
    hv.distance,
    hv.hit_type,
    hv.result,
    hv.days_from_assessment
FROM assessment_hittrax_view hv
WHERE hv.assessment_id = 42  -- Replace with actual assessment_id
ORDER BY hv.hittrax_session_date, hv.hittrax_timestamp;
```

### Scenario 6: Identify Players Needing Assessment

Find players who haven't been assessed recently.

```sql
SELECT 
    player_name,
    MAX(assessment_date) as last_assessment,
    DATEDIFF(CURDATE(), MAX(assessment_date)) as days_since_assessment,
    COUNT(*) as total_assessments,
    
    -- Has recent swing data without assessment
    (SELECT COUNT(DISTINCT session_date)
     FROM blast_swing_data bsd
     WHERE bsd.player_name = ha.player_name
     AND bsd.session_date > MAX(ha.assessment_date)
    ) as blast_sessions_since_assessment,
    
    (SELECT COUNT(DISTINCT session_date)
     FROM hittrax_swing_data hsd
     WHERE hsd.player_name = ha.player_name
     AND hsd.session_date > MAX(ha.assessment_date)
    ) as hittrax_sessions_since_assessment

FROM hitting_assessments ha

GROUP BY ha.player_name

HAVING DATEDIFF(CURDATE(), MAX(assessment_date)) > 30  -- No assessment in 30+ days

ORDER BY days_since_assessment DESC;
```

## 📊 Creating Views for Your Reports

If you frequently run the same report, create a view for it:

```sql
-- Example: Create a view for "ready for next assessment"
CREATE OR REPLACE VIEW players_ready_for_assessment AS
SELECT 
    ha.player_name,
    MAX(ha.assessment_date) as last_assessment,
    DATEDIFF(CURDATE(), MAX(ha.assessment_date)) as days_since,
    COUNT(DISTINCT bs.session_date) as swing_sessions_since
FROM hitting_assessments ha
LEFT JOIN blast_swing_data bs 
    ON ha.player_name = bs.player_name 
    AND bs.session_date > MAX(ha.assessment_date)
GROUP BY ha.player_name
HAVING days_since >= 14 AND swing_sessions_since >= 3;

-- Then query it simply:
SELECT * FROM players_ready_for_assessment;
```

## 🔄 Automating Reports

### Option 1: Scheduled MySQL Events

```sql
-- Create a summary table that gets updated daily
CREATE TABLE IF NOT EXISTS assessment_daily_summary (
    summary_date DATE PRIMARY KEY,
    total_assessments INT,
    total_players INT,
    avg_bat_speed DECIMAL(5,1),
    avg_exit_velo DECIMAL(5,1),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create event to populate it daily
CREATE EVENT IF NOT EXISTS update_daily_summary
ON SCHEDULE EVERY 1 DAY
STARTS '2024-01-01 00:00:00'
DO
INSERT INTO assessment_daily_summary (
    summary_date, 
    total_assessments, 
    total_players,
    avg_bat_speed,
    avg_exit_velo
)
SELECT 
    CURDATE(),
    COUNT(DISTINCT ha.assessment_id),
    COUNT(DISTINCT ha.player_name),
    AVG(bv.bat_speed),
    AVG(hv.exit_velocity)
FROM hitting_assessments ha
LEFT JOIN assessment_blast_view bv ON ha.assessment_id = bv.assessment_id
LEFT JOIN assessment_hittrax_view hv ON ha.assessment_id = hv.assessment_id
WHERE ha.assessment_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
ON DUPLICATE KEY UPDATE
    total_assessments = VALUES(total_assessments),
    total_players = VALUES(total_players),
    avg_bat_speed = VALUES(avg_bat_speed),
    avg_exit_velo = VALUES(avg_exit_velo),
    created_at = CURRENT_TIMESTAMP;
```

### Option 2: Python Script

Save this as `generate_report.py`:

```python
import mysql.connector
import pandas as pd
from datetime import datetime

# Connect to database
conn = mysql.connector.connect(
    host='your_host',
    user='your_user',
    password='your_password',
    database='your_database'
)

# Query for 4th assessment comparison
query = """
    -- Use one of the queries above
"""

# Execute and save to CSV
df = pd.read_sql(query, conn)
filename = f"assessment_report_{datetime.now().strftime('%Y%m%d')}.csv"
df.to_csv(filename, index=False)

print(f"Report saved: {filename}")
conn.close()
```

## 💡 Report Best Practices

1. **Use CTEs for Complex Queries**: Break down complex logic
   ```sql
   WITH current_assessments AS (
       SELECT * FROM player_assessment_history 
       WHERE assessment_number = 4
   ),
   blast_averages AS (
       SELECT assessment_id, AVG(bat_speed) as avg_speed
       FROM assessment_blast_view
       GROUP BY assessment_id
   )
   SELECT * FROM current_assessments ca
   JOIN blast_averages ba ON ca.assessment_id = ba.assessment_id;
   ```

2. **Add Indexes for Performance**:
   ```sql
   CREATE INDEX idx_assessment_player_date 
   ON hitting_assessments(player_name, assessment_date);
   ```

3. **Use EXPLAIN to Optimize**:
   ```sql
   EXPLAIN SELECT * FROM assessment_blast_view WHERE player_name = 'John Smith';
   ```

4. **Cache Expensive Calculations**: Create summary tables for frequently-accessed aggregations

5. **Parameterize Your Queries**: Use placeholders for player names, dates, etc.

## 📝 Next Steps

1. Test these queries with your actual data
2. Modify them to match your specific needs
3. Create views for your most common reports
4. Build a simple reporting tool or dashboard
5. Schedule automated report generation

Remember: These are starting points. Customize them based on your specific reporting requirements and the metrics that matter most to your program!
