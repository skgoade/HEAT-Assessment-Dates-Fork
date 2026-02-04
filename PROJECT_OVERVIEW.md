# Hitting Assessment System - Project Overview

## 🎯 Purpose

This system enables RBI Baseball Academy hitting trainers to record assessment data and automatically link it with existing Blast Motion and HitTrax swing data for comprehensive player development tracking and automated reporting.

## 🏗️ Architecture

### Components

1. **Database Layer**
   - MySQL database with assessment tracking table
   - Pre-built SQL views for data analysis
   - Auto-incrementing assessment IDs
   - Historical tracking and progression analysis

2. **Backend API**
   - Python Flask REST API
   - Deployed on Google Cloud Run
   - Handles CRUD operations for assessments
   - Secure credential management via Secret Manager

3. **Frontend**
   - Single-page HTML form
   - JavaScript for API communication
   - Autocomplete from historical data
   - Real-time validation and feedback

## 📊 Data Flow

```
Trainer Input → Frontend Form → Backend API → MySQL Database
                                                    ↓
                                    Database Views (Automatic Joins)
                                                    ↓
                        Assessment + Blast Data + HitTrax Data
                                                    ↓
                                        Automated Reports
```

## 🗄️ Database Design

### Core Table
- `hitting_assessments`: Stores assessment metadata with auto-increment ID

### Linking Views
1. **assessment_blast_view**: Joins assessments with Blast swing data
2. **assessment_hittrax_view**: Joins assessments with HitTrax swing data
3. **assessment_combined_view**: Combines all three data sources
4. **assessment_summary**: Aggregates session counts per assessment
5. **player_assessment_history**: Tracks progression over time

### Key Design Decisions

**Time Window Linking (±7 days)**
- Assessments link with swing data from 7 days before to 7 days after
- Configurable if your workflow requires different timing
- Rationale: Captures pre-assessment baseline and post-assessment work

**Player Name as Link Key**
- Uses player name for joining tables
- Requires consistent naming across systems
- Alternative: Could add player_id if available

**Auto-incrementing Assessment ID**
- Unique identifier for each assessment
- Enables easy tracking and comparison
- Simplifies automated report generation

## 📈 Use Cases

### 1. Single Assessment Review
Query one assessment and see all linked swing data from Blast and HitTrax.

### 2. Assessment Comparison
Compare player's 4th assessment to their 1st assessment to track long-term progress.

### 3. Recent Performance
Compare player's latest assessment to their previous assessment.

### 4. Progress Tracking
View complete assessment history with automatic numbering (1st, 2nd, 3rd, etc.).

### 5. Automated Reporting
Build reports that automatically pull assessment data and metrics from linked swing sessions.

## 🔄 Workflow

### For Trainers

1. **Conduct Assessment**: Work with player on hitting mechanics
2. **Record Assessment**: Fill out online form (2-3 minutes)
   - Player name
   - Assessment date
   - Trainer name (optional)
   - Notes (optional)
3. **Submit**: Get confirmation with Assessment ID
4. **Data Links Automatically**: System finds swing data within ±7 days

### For Analysis

1. **Query Views**: Use pre-built views to access linked data
2. **Generate Reports**: Compare assessments over time
3. **Track Progress**: Monitor player development metrics
4. **Identify Trends**: Spot areas of improvement or concern

## 🛠️ Technology Stack

- **Backend**: Python 3.11, Flask, MySQL Connector
- **Frontend**: HTML5, CSS3, Vanilla JavaScript
- **Database**: MySQL 5.7+
- **Deployment**: Google Cloud Run, Cloud Build, Secret Manager
- **Infrastructure**: Docker containerization

## 🔐 Security

- Environment variables and Secret Manager for credentials
- CORS configuration for frontend access
- HTTPS-only communication
- No sensitive data in frontend code
- Database credentials never exposed

## 📦 Project Structure

```
HittingAssessment/
├── backend/
│   ├── main.py              # Flask API
│   ├── requirements.txt     # Python dependencies
│   └── Dockerfile           # Container configuration
├── frontend/
│   ├── index.html           # Assessment form
│   └── (logo images)        # Branding assets
├── deployment/
│   ├── schema.sql           # Database schema and views
│   ├── deploy.sh            # Cloud Run deployment script
│   └── setup-secrets.sh     # Secret Manager setup
├── docs/
│   ├── QUICKSTART.md        # Quick setup guide
│   └── SCHEMA_CUSTOMIZATION.md  # Customization guide
├── README.md                # Main documentation
├── PROJECT_OVERVIEW.md      # This file
└── .gitignore               # Git ignore rules
```

## 🎓 Key Concepts

### Assessment Window
The ±7 day window allows capturing:
- **Pre-assessment baseline**: Sessions 1-7 days before show where player started
- **Post-assessment work**: Sessions 1-7 days after show implementation of feedback

### Assessment Numbering
The `player_assessment_history` view automatically numbers assessments:
- Helps identify milestone assessments (1st, 4th, 10th, etc.)
- Enables "first vs current" comparisons
- Tracks assessment frequency

### View-Based Architecture
Using database views instead of stored procedures:
- **Pros**: Easy to modify, no proc privileges needed, version controllable
- **Cons**: Recalculated on query (but performance impact is minimal)
- **Decision**: Views are better for this use case (infrequent queries, changing requirements)

## 🚀 Deployment Strategy

### Development
- Run backend locally with local MySQL
- Open frontend HTML directly in browser
- Test with sample data

### Production
- Backend on Google Cloud Run (auto-scaling)
- Frontend on any static hosting
- Database on Cloud SQL or external MySQL

## 🔮 Future Enhancements

Potential additions for future versions:

1. **Player ID System**: Add unique player IDs to handle name changes
2. **Assessment Templates**: Pre-defined assessment types (initial, progress, final)
3. **Photo Upload**: Add before/after video/photo upload capability
4. **Mobile App**: Native iOS/Android apps for trainers
5. **Dashboard**: Web dashboard for viewing all assessments
6. **Notifications**: Alert trainers when swing data is available
7. **Export**: PDF report generation from assessment data
8. **Multi-sport**: Expand to track pitching assessments too

## 📊 Metrics & Monitoring

### Key Metrics to Track

- Assessment submission frequency
- Average assessments per player
- Data linking success rate (% of assessments with swing data)
- Most active trainers
- Popular assessment dates/times

### Monitoring

- Cloud Run request metrics
- Database connection pool stats
- API response times
- Error rates

## 🤝 Integration Points

### Current Integrations
- Blast Motion swing data tables
- HitTrax swing data tables

### Potential Future Integrations
- Calendar systems (schedule assessments)
- Communication platforms (notify trainers)
- Video analysis software
- Athlete management systems

## 📝 Maintenance

### Regular Tasks
- Database backups (automated)
- Monitor Cloud Run costs
- Update Python dependencies quarterly
- Review and optimize slow queries

### Troubleshooting Resources
- Cloud Run logs
- MySQL slow query log
- Frontend browser console
- Health check endpoint

## 🎯 Success Metrics

This system is successful when:

1. **Adoption**: Trainers use it consistently for all assessments
2. **Data Quality**: 90%+ of assessments link with swing data
3. **Time Savings**: Reduces report generation time
4. **Insights**: Enables better player development decisions
5. **Reliability**: 99.5%+ uptime for form submissions

## 📞 Support & Documentation

- **README.md**: Comprehensive setup and usage guide
- **QUICKSTART.md**: Get running in 5 minutes
- **SCHEMA_CUSTOMIZATION.md**: Adapt to your database structure
- **Inline Comments**: Code is well-commented for maintenance

## 🏆 Project Goals

1. **Simplicity**: Minimal trainer input required
2. **Automation**: Automatic data linking and analysis
3. **Flexibility**: Easy to customize for different needs
4. **Reliability**: Production-ready and scalable
5. **Insight**: Enable data-driven player development

---

**Built for RBI Baseball Academy by Claude**
Version 1.0 - February 2024
