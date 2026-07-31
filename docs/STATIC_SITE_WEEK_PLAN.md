# HEAT Static Website Refinement — 10-Day Blueprint

**Decision:** Refine the existing static form ([heat-hitting-assessment-dates](https://storage.googleapis.com/heat-hitting-assessment-dates/index.html)).

**Source of product feedback:** [HEAT Assessment PDF Creation](https://docs.google.com/document/d/1gEOjLdDnvtyY0iyJEgfBxw5KEljA9TKBgshbL4w1x1w/edit?usp=sharing) (Noah comments incorporated below).

**Goal by day 10:** Trainer opens the site → enters athlete → if prior assessments exist in DB, show those dates to confirm; otherwise treat as Initial → submit → save flagged assessment → generate header+metrics PDF → local + GCS.

**Out of scope in these 10 days:** Slack-first capture, calendar/Statstak booking (Noah: longer-term / cost), VALD/questionnaire/narrative, sequence auto-detect, full example-PDF parity.

---

## Noah’s comments → what we changed

| Comment | Meaning | Adjustment |
|---------|---------|------------|
| **[b]** No way to “check baseline” | Don’t make trainers invent/select a special baseline field in a confusing way | **Auto-baseline** = earliest `initial` (or earliest assessment) for that player. Form focuses on **prior dates / previous retest**, not a separate baseline picker |
| **[c]** After name, if they’ve tested before, provide previous dates | Don’t make the trainer answer a yes/no we already know | After name, **lookup prior rows**. Empty → Initial. Has rows → show dates + treat as Retest (confirm previous). No “have they tested before?” question |
| **[d]** Assessment date is **one day**, not multiple | ±7 day window was wrong for v1 | Metrics join = **assessment calendar day only** (`00:00`–`23:59` local/ET on `assessment_date`) |
| **[e]** Use a Jupyter notebook + one player to validate queries/visuals before PDF | Don’t jump straight to ReportLab | **Days 3–4** include a notebook that proves Blast/HitTrax pulls for one athlete |
| **[f]** GCS paths must be easily identifiable later | Path design matters | Use `{player_slug}/{assessment_date}_{assessment_id}.pdf` (or similar readable layout) |
| **[a]** Calendar scheduling | Wanted long-term, not now | Stays in backlog |

---

## Product shape

```text
Open static site
  → Player name (autocomplete)
  → GET prior assessments for that player
       None  → Type = Initial (first logged assessment)
       Some  → Type = Retest; show prior dates; trainer confirms previous
             (baseline auto from earliest initial in history)
  → Assessment date + optional trainer/notes
  → Submit
  → API inserts hitting_assessments (+ type; previous_assessment_id when retest)
  → Aggregate Blast + HitTrax for assessment_date only (single day)
  → Baseline comparison auto from earliest initial (not a stored form field)
  → PDF (header + metrics) → local + GCS (readable object path)
  → Success UI: assessment ID + PDF link
```

---

## 10-day timeline

| Day | Focus | Milestone | Done when |
|-----|--------|-----------|-----------|
| **1** | Schema | M1a — DB flags | Type + previous/baseline IDs + `report_gcs_uri` on `hitting_assessments` |
| **2** | API | M1b — API contract | Create/list work; baseline can be auto-resolved server-side |
| **3** | Notebook + Blast | M2a — Validate Blast | Jupyter notebook for one player shows correct Blast day slice |
| **4** | Notebook + HitTrax | M2b — Full metrics | Same notebook + API metrics: current/previous/baseline for one day |
| **5** | PDF draft | M3a — Local PDF | Header + tables from validated queries |
| **6** | GCS + regen | M3b — Storage | Readable GCS paths; URI stored; regenerate works |
| **7** | Frontend core | M4a — Name → prior prompt | “Assessed before?” + prior date confirm |
| **8** | Frontend polish | M4b — Success + mobile | PDF link; mobile OK |
| **9** | Deploy | M5a — Prod | Cloud Run + form bucket updated |
| **10** | QA | M5b — Noah review | Real athlete PDF; gaps logged |

---

## Milestone details

### Days 1–2 — Data model + API (M1)

| Column | Purpose |
|--------|---------|
| `assessment_type` | `initial` \| `retest` |
| `previous_assessment_id` | Prior assessment trainer confirmed (optional on true first test) |
| `report_gcs_uri` | After PDF upload |

Baseline is **not** stored — metrics resolve earliest `initial` for the player at report time.

**API:** create/list + `GET .../metrics` + `POST .../report` (wired by day 6).

### Days 3–4 — Metric aggregation + notebook (M2)

**Window (locked per Noah [d]):** swings where `DATE(session) = assessment_date` only.

**Sources:** `blast_swing_metrics_PROD`; HitTrax via `hittrax_plays` + `hittrax_session` (silver as fallback).

**Notebook (`notebooks/heat_metrics_validation.ipynb` or under `docs/`):**  
Pick one athlete (e.g. Jason Peele 2026-01-12) → plot/print Blast + HitTrax aggregates → confirm before trusting PDF.

### Days 5–6 — PDF + storage (M3)

- Header + comparison tables (Current / Δ Prev / Δ Base when peers exist)
- Empty day → “—”, not a crash
- GCS path example: `heat-assessments/jason_peele/2026-01-12_2.pdf`

### Days 7–8 — Static site UX (M4)

1. Name autocomplete  
2. On name select → `GET /api/hitting-assessment/player/<name>`  
3. **No prior rows** → type = Initial (no date picker for previous)  
4. **Has prior rows** → type = Retest; show dates; trainer confirms previous (default = most recent)  
5. Assessment date / trainer / notes  
6. Success + PDF link  

No free-typed comparison dates. No yes/no “assessed before?” question — the DB answers that. No separate baseline picker.

### Days 9–10 — Ship & review (M5)

Deploy, smoke test, Noah review.

---

## Daily checklist

```text
[x] Day 1–2  Schema + API type/IDs (done on feature branch)
[x] Day 3–4  Metrics API day-only; HitTrax via plays+session; notebook at notebooks/heat_metrics_validation.ipynb
[x] Day 5    Local PDF via report_pdf + POST .../report
[x] Day 6    Readable GCS object keys; PDF on submit + regen; HEAT_GCS_BUCKET optional (live upload pending IAM)
[x] Day 7–8  Form: prior lookup → Initial/Retest + previous confirm; success + PDF link (frontend/index.html)
[ ] Day 9    Prod deploy (form bucket + Cloud Run + HEAT_GCS_BUCKET IAM)
[ ] Day 10   Noah review
```

---

## After day 10 (backlog)

- Calendar / Baseline booking (Noah [a] — longer term)
- Start/end **time** windows (not just date) if same-day training pollution appears
- VALD / coach narrative / questionnaire
- Zone/field HitTrax charts
- `player_directory` ID joins
- Slack start/stop

---

## Success definition (end of day 10)

1. Trainer never free-types comparison dates  
2. Prior history comes from DB lookup after name (not a yes/no prompt)  
3. Metrics use **assessment day only** (Noah [d])  
4. Queries validated in a notebook for one player (Noah [e])  
5. PDF in GCS under an identifiable path (Noah [f])  
6. Draft OK for internal review  
