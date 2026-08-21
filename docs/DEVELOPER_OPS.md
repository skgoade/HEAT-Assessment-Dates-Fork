# HEAT Assessment API — ops checklist

## Service

- **New** Cloud Run service: `heat-assessment-api` 
- Region: `us-east4`
- Image: `gcr.io/norse-coral-441421-r9/heat-assessment-api`

## Preferred deploy (Cloud Build builds + deploys)

From `backend/`:

```bash
gcloud builds submit --config cloudbuild.yaml .
```

The Cloud Build service account needs:

- Artifact Registry Writer (or Editor) to push the image
- Cloud Run Admin
- Service Account User on the Cloud Run runtime SA
- Secret Manager Secret Accessor for DB_* secrets and `ANTHROPIC_API_KEY`

Create the Anthropic key secret before the next Cloud Build deploy (deploy fails if a `--set-secrets` name is missing):

```bash
gcloud secrets create ANTHROPIC_API_KEY --project=norse-coral-441421-r9
# Real key, or a placeholder if drafts are not enabled yet:
printf '%s' 'YOUR_ANTHROPIC_API_KEY' | gcloud secrets versions add ANTHROPIC_API_KEY --data-file=- --project=norse-coral-441421-r9
```

Grant the Cloud Run runtime service account Secret Accessor on that secret if it is not already using a project-wide accessor role. Optional: set `HEAT_DRAFT_MODEL` on Cloud Run (default `claude-sonnet-4-6`) without a code change.

Without a real key, Draft buttons still show on the form; the API returns 503.



## IAM for human deployers (optional)

If deploying with `gcloud run deploy` as a user:

```bash
gcloud projects add-iam-policy-binding norse-coral-441421-r9 \
  --member="user:EMAIL" \
  --role="roles/artifactregistry.reader"

gcloud projects add-iam-policy-binding norse-coral-441421-r9 \
  --member="user:EMAIL" \
  --role="roles/run.admin"
```



## SQL migrations (PlayerDev) — run once each if missing

```bash
# Attachments (trainer visuals) — CONFIRMED present in prod (attachments API works)
# deployment/assessment_attachments.sql

# PDF version history — REQUIRED before version rows persist (API falls back to latest URI until applied)
# deployment/assessment_report_versions.sql

# Per-phase mechanics notes (JSON) for PDF phase cards
# deployment/hitting_assessments_mechanics_phase_notes.sql

# Mechanical Observation + Force-Plate Metrics Summary text boxes on the PDF
# deployment/hitting_assessments_pdf_summaries.sql

# Training Focus card on PDF page 1
# deployment/hitting_assessments_training_focus.sql

# player_directory height/weight for the PDF header (probe columns first)
# deployment/player_directory_height_weight.sql
```

Verify with `[deployment/verify_heat_schema.sql](../deployment/verify_heat_schema.sql)`:

```sql
SHOW TABLES LIKE 'assessment_attachments';
SHOW TABLES LIKE 'assessment_report_versions';
SHOW COLUMNS FROM hitting_assessments LIKE 'mechanics_phase_notes';
SHOW COLUMNS FROM hitting_assessments LIKE 'mechanical_summary';
SHOW COLUMNS FROM hitting_assessments LIKE 'best_of_day_summary';
SHOW COLUMNS FROM hitting_assessments LIKE 'training_focus';
```

**Status (2026-08-08):** `assessment_attachments` is live. `assessment_report_versions` was **not** present yet — run that SQL on PlayerDev, then redeploy is optional (code already tolerates missing table for reads).

**Status (2026-08-13):** run `hitting_assessments_mechanics_phase_notes.sql` before using phase-card notes (create/regen will fail SELECT/INSERT until the column exists).

**Status (2026-08-14):** run `hitting_assessments_pdf_summaries.sql` before Mechanical Observation / Force-Plate Metrics Summary persist. Run `hitting_assessments_training_focus.sql` before Training Focus persists. Run `player_directory_height_weight.sql` after probing `SHOW COLUMNS FROM player_directory` (skip if height/weight already exist).
## GCS bucket `heat-assessment-reports`

- Keep **private** (signed URLs from the API).
- Grant the Cloud Run runtime service account:
  - `roles/storage.objectAdmin` on the bucket (or objectCreator + objectViewer)
  - `roles/iam.serviceAccountTokenCreator` on **itself** (required for V4 signed URLs via IAM signBlob)

```bash
# Example — replace RUNTIME_SA_EMAIL with the Cloud Run SA
gsutil iam ch serviceAccount:RUNTIME_SA_EMAIL:roles/storage.objectAdmin \
  gs://heat-assessment-reports

gcloud iam service-accounts add-iam-policy-binding RUNTIME_SA_EMAIL \
  --member="serviceAccount:RUNTIME_SA_EMAIL" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project=norse-coral-441421-r9
```

Optional: set `HEAT_GCS_SIGNER_SA` on Cloud Run to that SA email if auto-detect fails.

## After deploy

1. Copy Service URL from Cloud Run.
2. In `frontend/index.html`, set before the main script (or edit `API_BASE`):

```html
<script>window.HEAT_API_BASE = 'https://heat-assessment-api-….run.app';</script>
```

1. Publish the updated form HTML to the static host / GCS form bucket.
2. Health: `curl https://…/health`



## Product sign-off

- **EV × LA**: dual y-axis (EV mph + distance ft) vs launch angle in `backend/report_charts.py`. Session-level predicted-carry / peak-LA overlay is deferred until a larger-sample model exists.

