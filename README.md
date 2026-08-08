# RBI Hitting Assessment System

A comprehensive system for tracking hitting assessments and automatically linking them with Blast Motion, HitTrax, and VALD data for player development reporting.

## For trainers

**[Trainer Guide](docs/TRAINER_GUIDE.md)** — how to submit assessments, upload mechanics photos, regenerate PDFs, and what shows up in the report.

## Overview

This system allows hitting trainers to input assessment data that is automatically linked with swing data from Blast Motion and HitTrax devices. The database views enable powerful automated reporting that compares:
- Current assessment to previous assessment
- 4th assessment to 1st assessment
- 4th assessment to most recent assessment
- Assessment progress over time

## 📋 Features

- **Simple Assessment Entry**: Static web form for player, date, Initial/Retest, and comparison links
- **Draft PDF reports**: Blast/HitTrax/VALD tables + charts; upload to private GCS with signed open links
- **PDF version history**: each regenerate keeps prior PDFs (`assessment_report_versions`)
- **Trainer visuals**: mechanics + extra photo uploads with captions
- **Auto-Incrementing IDs**: Automatic unique assessment ID generation
- **Data Linking**: Assessment-day joins with Blast, HitTrax, and VALD (PDF pipeline)
- **Multiple Views**: Pre-built SQL views for different analysis needs
- **REST API**: Assessment create/read, attachments, report regenerate, report versions
- **Autocomplete**: Player and trainer name suggestions from history
- **Progress Tracking**: Built-in assessment numbering and history tracking


## 🗄️ Database Schema

### Main Table: `hitting_assessments`

| Column | Type | Description |
|--------|------|-------------|
| assessment_id | INT (PK, AUTO_INCREMENT) | Unique assessment identifier |
| assessment_date | DATE | Date of the assessment |
| player_name | VARCHAR(255) | Name of the player |
| trainer_name | VARCHAR(255) | Name of the trainer (optional) |
| notes | TEXT | Assessment notes/observations (optional) |
| created_at | TIMESTAMP | Record creation timestamp |
| updated_at | TIMESTAMP | Record update timestamp |

### Database Views

#### 1. `assessment_blast_view`
Joins hitting assessments with Blast Motion swing data. Links assessments to Blast sessions within ±7 days.

**Key Columns:**
- All assessment fields
- Blast metrics: bat_speed, peak_hand_speed, attack_angle, power, etc.
- days_from_assessment: Days between assessment and Blast session

#### 2. `assessment_hittrax_view`
Joins hitting assessments with HitTrax swing data. Links assessments to HitTrax sessions within ±7 days.

**Key Columns:**
- All assessment fields
- HitTrax metrics: exit_velocity, launch_angle, distance, spray_direction, etc.
- days_from_assessment: Days between assessment and HitTrax session

#### 3. `assessment_combined_view`
Combines Blast and HitTrax data by matching on timestamp (ts field), then joins with assessments.

**Key Features:**
- Side-by-side Blast and HitTrax metrics
- Swing quality tier classification
- Combined analysis of bat speed, exit velocity, and other metrics

#### 4. `assessment_summary`
Summary view showing count of linked Blast and HitTrax sessions per assessment.

#### 5. `player_assessment_history`
Historical view with assessment numbering and progression tracking.

**Key Features:**
- Assessment number (1st, 2nd, 3rd, etc.)
- Days since last assessment
- First and most recent assessment dates

## 🚀 Getting Started

### Prerequisites

- MySQL 5.7+ or MySQL 8.0+
- Python 3.11+
- Google Cloud Platform account (for deployment)
- Existing tables: `blast_swing_data` and `hittrax_swing_data`

### Installation

1. **Clone or download this repository**

2. **Set up the database**
   ```bash
   mysql -u your_user -p your_database < deployment/schema.sql
   ```

3. **Configure your environment**
   - Update `deployment/setup-secrets.sh` with your GCP project ID
   - Update `deployment/deploy.sh` with your GCP project ID and region

4. **Set up Google Cloud secrets**
   ```bash
   chmod +x deployment/setup-secrets.sh
   ./deployment/setup-secrets.sh
   ```

5. **Deploy the backend**
   ```bash
   chmod +x deployment/deploy.sh
   ./deployment/deploy.sh
   ```

6. **Update frontend configuration**
   - Edit `frontend/index.html`
   - Update the `API_URL` variable with your Cloud Run service URL

7. **Deploy the frontend**
   - Upload `frontend/index.html` and logo images to your web hosting
   - Or use Google Cloud Storage, Firebase Hosting, etc.

## 📊 Database View Assumptions

The views make the following assumptions about your existing tables:

### Blast Motion Table (`blast_swing_data`)
Expected columns:
- `player_name` - VARCHAR
- `session_date` - DATE
- `ts` - TIMESTAMP (for matching with HitTrax)
- Metric columns: `bat_speed`, `peak_hand_speed`, `attack_angle`, `power`, etc.

### HitTrax Table (`hittrax_swing_data`)
Expected columns:
- `player_name` - VARCHAR
- `session_date` - DATE
- `ts` - TIMESTAMP (for matching with Blast)
- Metric columns: `exit_velocity`, `launch_angle`, `distance`, etc.

**⚠️ Important**: If your column names are different, you'll need to modify the views in `deployment/schema.sql` to match your actual schema.

## 🔌 API Endpoints

### POST /api/hitting-assessment
Submit a new hitting assessment.

**Request Body:**
```json
{
  "playerName": "John Smith",
  "assessmentDate": "2024-02-04",
  "trainerName": "Coach Williams",
  "notes": "Focus on launch angle improvement"
}
```

**Response:**
```json
{
  "success": true,
  "assessment_id": 42,
  "player": "John Smith",
  "assessment_date": "2024-02-04"
}
```

### GET /api/hitting-assessment/{assessment_id}
Get a specific assessment by ID.

### GET /api/hitting-assessment/player/{player_name}
Get all assessments for a specific player.

### GET /api/hitting-assessment/recent?limit=50
Get recent assessments (last 30 days).

### GET /health
Health check endpoint.

## 📈 Example Queries for Automated Reports

### Compare 4th Assessment to 1st and Most Recent

```sql
SELECT 
    current.assessment_id,
    current.player_name,
    current.assessment_date as current_date,
    current.assessment_number,
    
    -- First assessment comparison
    first.assessment_date as first_assessment_date,
    first.notes as first_notes,
    DATEDIFF(current.assessment_date, first.assessment_date) as days_since_first,
    
    -- Previous assessment comparison
    prev.assessment_date as previous_assessment_date,
    prev.notes as previous_notes,
    current.days_since_last_assessment,
    
    -- Current assessment
    current.notes as current_notes
FROM player_assessment_history current
LEFT JOIN player_assessment_history first 
    ON current.player_name = first.player_name 
    AND first.assessment_number = 1
LEFT JOIN player_assessment_history prev 
    ON current.player_name = prev.player_name 
    AND prev.assessment_number = current.assessment_number - 1
WHERE current.assessment_number = 4;
```

### Get Assessment with Average Blast Metrics

```sql
SELECT 
    ha.assessment_id,
    ha.player_name,
    ha.assessment_date,
    COUNT(bsd.session_date) as blast_sessions,
    AVG(bsd.bat_speed) as avg_bat_speed,
    AVG(bsd.attack_angle) as avg_attack_angle,
    AVG(bsd.power) as avg_power
FROM hitting_assessments ha
LEFT JOIN blast_swing_data bsd 
    ON ha.player_name = bsd.player_name
    AND ABS(DATEDIFF(bsd.session_date, ha.assessment_date)) <= 7
WHERE ha.assessment_id = ?
GROUP BY ha.assessment_id, ha.player_name, ha.assessment_date;
```

### Get Assessment with Average HitTrax Metrics

```sql
SELECT 
    ha.assessment_id,
    ha.player_name,
    ha.assessment_date,
    COUNT(hsd.session_date) as hittrax_sessions,
    AVG(hsd.exit_velocity) as avg_exit_velo,
    AVG(hsd.launch_angle) as avg_launch_angle,
    AVG(hsd.distance) as avg_distance
FROM hitting_assessments ha
LEFT JOIN hittrax_swing_data hsd 
    ON ha.player_name = hsd.player_name
    AND ABS(DATEDIFF(hsd.session_date, ha.assessment_date)) <= 7
WHERE ha.assessment_id = ?
GROUP BY ha.assessment_id, ha.player_name, ha.assessment_date;
```

## 🛠️ Customization

### Adjusting the Time Window
The views currently link assessments with swing data within ±7 days. To change this:

Edit the `schema.sql` file and modify:
```sql
AND ABS(DATEDIFF(bsd.session_date, ha.assessment_date)) <= 7
```

Change `7` to your desired number of days.

### Adding Custom Columns
To add custom fields to the assessment form:

1. Add column to database table in `schema.sql`
2. Add form field in `frontend/index.html`
3. Update validation in `backend/main.py` (validate_assessment_data)
4. Update INSERT query in `backend/main.py` (submit_assessment)

### Modifying View Columns
Edit the CREATE OR REPLACE VIEW statements in `schema.sql` to match your specific Blast and HitTrax table structures.

## 📱 Frontend Features

- **Auto-complete**: Player and trainer names from recent assessments
- **Date Validation**: Prevents future dates
- **Auto-capitalization**: Automatically capitalizes names
- **Real-time Feedback**: Success/error messages
- **Responsive Design**: Works on mobile and desktop
- **Loading States**: Visual feedback during submission

## 🔒 Security Notes

- Backend uses environment variables and Secret Manager for credentials
- CORS is enabled - update allowed origins in production
- Consider adding authentication for production use
- Rate limiting should be implemented for public endpoints

## 🐛 Troubleshooting

### Common Issues

**Views fail to create:**
- Check that your Blast and HitTrax tables exist
- Verify column names match what's in the views
- Check that you have CREATE VIEW permissions

**API connection errors:**
- Verify the API_URL in frontend/index.html is correct
- Check that Cloud Run service is deployed and running
- Verify CORS settings in backend/main.py

**No data appearing in views:**
- Verify player_name matches exactly between tables
- Check date ranges (±7 days by default)
- Ensure swing data exists in the time window

## 📞 Support

For issues or questions:
1. Check the database logs for errors
2. Check Cloud Run logs: `gcloud run logs read hitting-assessment-api --region us-central1`
3. Verify database connection from Cloud Run service

## 📄 License

This project is proprietary to RBI Baseball Academy.

## 🙏 Acknowledgments

Built for RBI Baseball Academy hitting program to streamline assessment tracking and automated reporting.
