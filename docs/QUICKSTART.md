# Quick Start Guide - Hitting Assessment System

## 🚀 5-Minute Setup

Follow these steps to get your hitting assessment system up and running quickly.

### Step 1: Database Setup (2 minutes)

```bash
# Connect to your MySQL database
mysql -u your_username -p your_database

# Run the schema file
source deployment/schema.sql

# Verify tables were created
SHOW TABLES LIKE 'hitting_assessments';
SHOW TABLES LIKE 'assessment%';
```

**Important**: Before running the schema, make sure you have:
- Existing `blast_swing_data` table
- Existing `hittrax_swing_data` table

If your table names or column names are different, edit `deployment/schema.sql` first!

### Step 2: Deploy Backend (2 minutes)

```bash
# Prefer Cloud Build build+deploy (see docs/NOAH_OPS.md)
cd backend
gcloud builds submit --config cloudbuild.yaml .
cd ..

# Or: bash ./deployment/deploy.sh
```

After deployment completes, note the service URL (currently
`https://heat-assessment-api-gyhwqhslwq-uk.a.run.app`).

Also run once on PlayerDev if missing:
- `deployment/assessment_attachments.sql`
- `deployment/assessment_report_versions.sql`

### Step 3: Configure Frontend (1 minute)

`frontend/index.html` defaults to the live `heat-assessment-api` URL.
For a local backend, add before the main script:

```html
<script>window.HEAT_API_BASE = 'http://localhost:8080';</script>
```

Do **not** point PDF work at legacy `hitting-assessment-api`.

**Local testing (API + form on your machine):** see [LOCAL_TESTING.md](LOCAL_TESTING.md).

### Step 4: Test It!

1. **Open the form**: Open `frontend/index.html` in your browser
2. **Fill it out**:
   - Player Name: Test Player
   - Assessment Date: Today's date
   - Trainer Name: Your name (optional)
   - Notes: "Initial test assessment"
3. **Submit**: Click "Submit Assessment"
4. **Verify**: You should see a success message with an assessment ID

### Step 5: Verify Database

```sql
-- Check the assessment was saved
SELECT * FROM hitting_assessments ORDER BY assessment_id DESC LIMIT 1;

-- Check if it's linking with your Blast data (adjust date as needed)
SELECT * FROM assessment_blast_view 
WHERE assessment_id = 1;  -- Use your actual assessment_id

-- Check if it's linking with your HitTrax data
SELECT * FROM assessment_hittrax_view 
WHERE assessment_id = 1;
```

## ✅ You're Done!

Your system is now ready to use. Trainers can access the form at the frontend URL.

## 🎯 Next Steps

### For Daily Use
1. **Deploy Frontend**: Upload `frontend/index.html` to your web server
2. **Bookmark**: Trainers should bookmark the form URL
3. **Train Staff**: Share [docs/TRAINER_GUIDE.md](TRAINER_GUIDE.md) (submit flow, photos, regen, PDF contents)

### For Automated Reports

Use the views to build your reports:

```sql
-- Example: Get player's assessment history with averages
SELECT 
    ha.assessment_id,
    ha.assessment_date,
    ha.assessment_number,
    ha.notes,
    AVG(bv.bat_speed) as avg_bat_speed,
    AVG(hv.exit_velocity) as avg_exit_velocity
FROM player_assessment_history ha
LEFT JOIN assessment_blast_view bv ON ha.assessment_id = bv.assessment_id
LEFT JOIN assessment_hittrax_view hv ON ha.assessment_id = hv.assessment_id
WHERE ha.player_name = 'John Smith'
GROUP BY ha.assessment_id, ha.assessment_date, ha.assessment_number, ha.notes
ORDER BY ha.assessment_date;
```

## 🐛 Quick Troubleshooting

**Form won't submit?**
- Check the browser console (F12) for errors
- Verify the API_URL is correct in index.html
- Test the backend health: `curl https://your-url.run.app/health`

**Views return no data?**
- Check that player names match exactly between tables
- Verify swing data exists on the assessment calendar day
- Check column names match your actual Blast/HitTrax tables

**Database connection failed?**
- Verify secrets are set: `gcloud secrets list`
- Check Cloud Run logs: `gcloud run logs read heat-assessment-api --region us-east4`
- Test database connection from Cloud Shell

## 📞 Need Help?

1. Check the full README.md for detailed documentation
2. Review the schema.sql comments for view explanations
3. Check Cloud Run logs for backend errors
4. Verify your Blast and HitTrax table structures match the views

## 🎓 Understanding the System

### How Assessment Linking Works

When you create an assessment with date `2024-02-04`:

1. **Blast View**: Finds Blast sessions for that player on `2024-02-04` (assessment day only)
2. **HitTrax View**: Finds HitTrax sessions for that player on the same day
3. **Combined View**: Matches Blast and HitTrax swings using the `ts` timestamp field

### Assessment Numbering

The `player_assessment_history` view automatically numbers each player's assessments:
- 1st assessment, 2nd assessment, 3rd assessment, etc.
- Calculates days between assessments
- Tracks first and most recent assessment dates

This makes it easy to build reports comparing "4th assessment vs 1st assessment."

## 💡 Pro Tips

1. **Consistent Naming**: Make sure player names are entered the same way every time
2. **Same Day Assessments**: Schedule hitting sessions on or near assessment days for better data linking
3. **Regular Backups**: Back up your database regularly
4. **Monitor Usage**: Check Cloud Run metrics to monitor form submissions
5. **Update Regularly**: Keep your views updated as you add new Blast/HitTrax metrics

---

**You're all set!** Start tracking assessments and building better reports. 🎉
