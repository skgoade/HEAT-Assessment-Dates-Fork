# HEAT Assessment API — ops checklist (Noah / deployers)

## Service
- **New** Cloud Run service: `heat-assessment-api` (do not overwrite legacy `hitting-assessment-api`)
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
- Secret Manager Secret Accessor for DB_* secrets

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
```

Verify with [`deployment/verify_heat_schema.sql`](../deployment/verify_heat_schema.sql):

```sql
SHOW TABLES LIKE 'assessment_attachments';
SHOW TABLES LIKE 'assessment_report_versions';
```

**Status (2026-08-08):** `assessment_attachments` is live. `assessment_report_versions` was **not** present yet — run that SQL on PlayerDev, then redeploy is optional (code already tolerates missing table for reads).

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

3. Publish the updated form HTML to the static host / GCS form bucket.
4. Health: `curl https://…/health`

## Product sign-off

- **EV × LA optimal LA band**: hang-time proxy in `backend/report_charts.py` (documented in `docs/TRAINER_GUIDE.md`). Confirm or replace with Noah’s preferred rule.
