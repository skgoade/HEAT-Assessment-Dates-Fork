# HEAT Static Site — 10-Day Plan (Overview)

Refine the existing assessment form so trainers confirm prior dates when needed and get an auto-generated header + metrics PDF in GCS.

Incorporates Noah’s notes from [HEAT Assessment PDF Creation](https://docs.google.com/document/d/1gEOjLdDnvtyY0iyJEgfBxw5KEljA9TKBgshbL4w1x1w/edit?usp=sharing): day-only data window, prior dates from DB after name entry, notebook validation before PDF, readable GCS paths. Baseline is auto (earliest initial).

---

## Days 1–2 — Data model & API

- Add assessment type and previous/baseline links on assessment records
- Update create/list APIs (baseline may be filled automatically on retest)
- Confirm inserts work for an Initial and a linked Retest

## Days 3–4 — Pull Blast & HitTrax metrics

- Join swing data by player + **assessment calendar day only** (not ±7 days)
- Build current / previous / baseline metric summaries
- Validate in a **Jupyter notebook** for one real athlete before trusting the PDF

## Days 5–6 — PDF & cloud storage

- Generate a simple comparison PDF (header + key tables)
- Upload to GCS with an **identifiable path** (player / date / id)
- Support re-running the report for an existing assessment

## Days 7–8 — Website updates

- After athlete name: look up prior assessments in the DB
- No priors → Initial; has priors → Retest and show dates to confirm previous
- Success state with assessment ID and PDF link (mobile-friendly)

## Days 9–10 — Ship & review

- Deploy API and publish the updated form
- End-to-end smoke test on production
- Review a real PDF with Noah; note follow-ups for later

---

## Done when

- No hand-typed comparison dates
- Assessment-day-only metrics; notebook-proven for one player
- PDF generates on submit and lands in GCS under a clear path
- Draft is good enough for internal review
