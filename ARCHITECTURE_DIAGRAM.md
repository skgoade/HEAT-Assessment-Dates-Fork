# System Architecture Diagram

## 📊 Data Flow Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          HITTING ASSESSMENT SYSTEM                       │
└─────────────────────────────────────────────────────────────────────────┘

1. TRAINER INPUT
   ┌──────────────┐
   │   Trainer    │
   │  Opens Form  │
   └──────┬───────┘
          │
          ▼
   ┌──────────────────────┐
   │   Frontend (HTML)    │
   │  - Player Name       │
   │  - Assessment Date   │
   │  - Trainer Name      │
   │  - Notes             │
   └──────┬───────────────┘
          │
          │ POST /api/hitting-assessment
          ▼

2. API PROCESSING
   ┌──────────────────────┐
   │  Backend (Flask)     │
   │  - Validates data    │
   │  - Connects to DB    │
   │  - Inserts record    │
   │  - Returns ID        │
   └──────┬───────────────┘
          │
          ▼

3. DATABASE STORAGE
   ┌─────────────────────────────────────────┐
   │         hitting_assessments             │
   │  ┌────────────────────────────────────┐ │
   │  │ assessment_id: 42 (AUTO_INCREMENT) │ │
   │  │ assessment_date: 2024-02-01        │ │
   │  │ player_name: "John Smith"          │ │
   │  │ trainer_name: "Coach Williams"     │ │
   │  │ notes: "Focus on launch angle"     │ │
   │  └────────────────────────────────────┘ │
   └─────────────────────────────────────────┘
          │
          │ Automatic View Processing
          ▼

4. DATA LINKING (Views automatically join data)

   ┌──────────────────────────────────────────────────┐
   │             ASSESSMENT #42                       │
   │          (John Smith, Feb 1, 2024)              │
   └────────────┬─────────────────────────────────────┘
                │
    ┌───────────┴───────────────┐
    │                           │
    ▼                           ▼
┌─────────────────┐    ┌──────────────────┐
│  Blast Data     │    │  HitTrax Data    │
│  Jan 25-Feb 8   │    │  Jan 25-Feb 8    │
│  (±7 days)      │    │  (±7 days)       │
└─────────────────┘    └──────────────────┘
    │                           │
    │  ┌────────────────────┐   │
    └──┤  Combined View     ├───┘
       │  (matched on ts)   │
       └────────────────────┘
                │
                ▼

5. QUERY & ANALYSIS
   ┌──────────────────────────────────────┐
   │         Automated Reports            │
   │                                      │
   │  • Compare 4th to 1st assessment    │
   │  • Compare current to previous       │
   │  • Track player progress over time   │
   │  • Team summary reports              │
   └──────────────────────────────────────┘
```

## 🗄️ Database View Structure

```
hitting_assessments (Main Table)
         │
         │ (joins on player_name + date range)
         │
    ┌────┴─────┬──────────────────────┐
    │          │                      │
    ▼          ▼                      ▼
┌─────────┐  ┌──────────┐      ┌───────────┐
│ Blast   │  │ HitTrax  │      │ Combined  │
│  View   │  │   View   │      │   View    │
└─────────┘  └──────────┘      └───────────┘
    │            │                    │
    │            │      ┌─────────────┘
    │            │      │ (joins Blast + HitTrax on timestamp)
    │            │      │
    ▼            ▼      ▼
┌────────────────────────────┐
│    Your Analysis Queries   │
│  (Compare assessments)     │
└────────────────────────────┘
```

## 📅 Time Window Explanation

```
Assessment Date: Feb 1, 2024
                     │
        ┌────────────┼────────────┐
        │            │            │
     Jan 25       Feb 1        Feb 8
        │            │            │
    ┌───▼────────────▼────────────▼───┐
    │     ±7 Day Window                │
    │                                  │
    │  All Blast sessions in range    │
    │  All HitTrax sessions in range  │
    │                                  │
    │  Automatically linked to         │
    │  Assessment #42                  │
    └──────────────────────────────────┘

Timeline View:
─────────────────────────────────────────────
    Jan 25   Jan 30   Feb 1   Feb 3   Feb 8
      │        │        │       │       │
   [Blast]  [HitTrax] [ASSESS][Blast][HitTrax]
      │        │        │       │       │
      └────────┴────────┴───────┴───────┘
              All linked to
            Assessment #42
```

## 🔗 How the Three Views Differ

```
1. ASSESSMENT_BLAST_VIEW
   ┌──────────────┐      ┌─────────────┐
   │ Assessment   ├──────▶ Blast Data  │
   │   #42        │      │ (all swings)│
   └──────────────┘      └─────────────┘
   
   Returns: Every Blast swing from Jan 25-Feb 8
   Use when: You want Blast-only analysis


2. ASSESSMENT_HITTRAX_VIEW
   ┌──────────────┐      ┌──────────────┐
   │ Assessment   ├──────▶ HitTrax Data │
   │   #42        │      │ (all swings) │
   └──────────────┘      └──────────────┘
   
   Returns: Every HitTrax swing from Jan 25-Feb 8
   Use when: You want HitTrax-only analysis


3. ASSESSMENT_COMBINED_VIEW
   ┌──────────────┐      
   │ Assessment   │      
   │   #42        │      
   └──────┬───────┘      
          │
    ┌─────┴────────┐
    │              │
    ▼              ▼
┌─────────┐   ┌──────────┐
│ Blast   │   │ HitTrax  │
│  Data   │   │   Data   │
└────┬────┘   └────┬─────┘
     │             │
     └──────┬──────┘
            │ (match on timestamp)
            ▼
    ┌───────────────┐
    │ Matched Swings│
    │ Only swings   │
    │ captured by   │
    │ BOTH systems  │
    └───────────────┘

   Returns: Only swings where Blast AND HitTrax captured the same swing
   Use when: You want complete picture (bat speed + exit velo for same swing)
```

## 🎯 Example Data Flow

```
STEP BY STEP:

1. Trainer submits form on Feb 1, 2024
   Player: "John Smith"
   
   ↓

2. Database creates record
   assessment_id: 42 (auto-generated)
   
   ↓

3. Views automatically find linked data:

   Blast Sessions Found:
   ┌─────────────────────────────┐
   │ Jan 28 - 120 swings         │
   │ Jan 30 - 85 swings          │
   │ Feb 2  - 100 swings         │
   │ Feb 5  - 95 swings          │
   └─────────────────────────────┘
   Total: 400 rows in assessment_blast_view
   
   HitTrax Sessions Found:
   ┌─────────────────────────────┐
   │ Jan 28 - 90 swings          │
   │ Jan 30 - 70 swings          │
   │ Feb 3  - 80 swings          │
   └─────────────────────────────┘
   Total: 240 rows in assessment_hittrax_view
   
   Combined (matched swings only):
   ┌─────────────────────────────┐
   │ Jan 28 - 85 matched         │
   │ Jan 30 - 65 matched         │
   │ Feb 3  - 75 matched         │
   └─────────────────────────────┘
   Total: 225 rows in assessment_combined_view
   
   ↓

4. You query for analysis:

   Query: "Get average bat speed for assessment 42"
   
   SELECT AVG(bat_speed) 
   FROM assessment_blast_view 
   WHERE assessment_id = 42;
   
   Result: 72.5 mph (average of 400 swings)
   
   ↓

5. Compare to previous assessment:

   Query: "Compare assessment 42 to assessment 38"
   
   Assessment 38 average: 70.2 mph
   Assessment 42 average: 72.5 mph
   Improvement: +2.3 mph
```

## 📊 Report Generation Flow

```
┌────────────────────────────────────────────────────┐
│           AUTOMATED REPORT GENERATION              │
└────────────────────────────────────────────────────┘

Player has 4 assessments:
├── Assessment #1 (Jan 1)  ──┐
├── Assessment #2 (Jan 15)   │
├── Assessment #3 (Jan 29)   │  Comparison
└── Assessment #4 (Feb 12) ──┘

Report Query Logic:
1. Get Assessment #4 data
   ↓
2. Get Assessment #1 data (first)
   ↓
3. Join with Blast/HitTrax views for both
   ↓
4. Calculate averages for each period
   ↓
5. Compute differences
   ↓
6. Generate report showing:
   ┌──────────────────────────────┐
   │ First Assessment (Jan 1):    │
   │   Bat Speed: 68.2 mph        │
   │   Exit Velo: 82.5 mph        │
   │                              │
   │ Current Assessment (Feb 12): │
   │   Bat Speed: 73.1 mph        │
   │   Exit Velo: 87.3 mph        │
   │                              │
   │ IMPROVEMENT:                 │
   │   Bat Speed: +4.9 mph ✓      │
   │   Exit Velo: +4.8 mph ✓      │
   │   Time Span: 42 days         │
   └──────────────────────────────┘
```

## 🔄 Summary: What Happens Automatically

```
When you create an assessment...

1. ✓ Unique ID assigned (auto-increment)
2. ✓ Record saved to database
3. ✓ Views find matching Blast data (±7 days)
4. ✓ Views find matching HitTrax data (±7 days)
5. ✓ Views match Blast+HitTrax swings (timestamp)
6. ✓ Data ready for queries

You just query the views - no manual joining needed!
```

## 💡 Key Takeaways

1. **One Form Submission** → Automatic linking with multiple data sources
2. **Three Views** → Three different ways to analyze the same assessment
3. **Time Window** → Captures context before and after assessment
4. **Auto-ID** → Easy to reference and compare assessments
5. **Pre-built Queries** → Just customize the SQL examples provided

---

This system handles all the complex joins for you. You focus on the analysis!
