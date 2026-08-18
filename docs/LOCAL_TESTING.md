# Local form testing guide

How to run the HEAT assessment **API on your machine** and exercise the form against it (PlayerDev via Cloud SQL Auth Proxy).

For trainer how-to (what fields mean), see [TRAINER_GUIDE.md](TRAINER_GUIDE.md).  
For production deploy, see [DEVELOPER_OPS.md](DEVELOPER_OPS.md).

---

## What you need

- Python 3.11+ (3.12/3.14 usually fine)
- `gcloud` logged in (`gcloud auth login` + `gcloud auth application-default login`)
- Cloud SQL Auth Proxy (or another way to reach PlayerDev MySQL on `127.0.0.1:3306`)
- DB credentials for PlayerDev (`DB_USER`, `DB_PASS`, database usually `PlayerDev`)
- This repo checked out

Optional for GCS uploads from local:

- Access to bucket `heat-assessment-reports`
- Application Default Credentials that can write to that bucket

Without GCS env vars, PDFs still generate under `backend/reports/` and the new download endpoint can serve them from `file://` paths.

---



## 1. One-time: Python deps

```powershell
cd C:\Users\skgoa\OneDrive\Documents\Code\HEAT-Assessment-Dates\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---



## 2. Start Cloud SQL Auth Proxy (terminal 1)

Use whatever connection name Noah gave you for PlayerDev. Example pattern:

```powershell
& C:\Users\skgoa\cloud-sql-proxy.exe norse-coral-441421-r9:us-east4:replay-baseball-player-dev --port 3306
```

Leave this running. The API will talk to `127.0.0.1:3306`.

Confirm you can connect (optional):

```powershell
mysql -h 127.0.0.1 -P 3306 -u YOUR_USER -p PlayerDev
```

---



## 3. Start the API (terminal 2)

```powershell
cd C:\Users\skgoa\OneDrive\Documents\Code\HEAT-Assessment-Dates
.\.venv\Scripts\Activate.ps1

$gcloud = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$env:DB_HOST = "127.0.0.1"
$env:DB_PORT = "3306"
$env:DB_NAME_PROD = "PlayerDev"
$env:DB_USER = (& $gcloud secrets versions access latest --secret=DB_USER --project=norse-coral-441421-r9).Trim()
$env:DB_PASS = (& $gcloud secrets versions access latest --secret=DB_PASS --project=norse-coral-441421-r9).Trim()
$env:HEAT_GCS_BUCKET = "heat-assessment-reports"
$env:HEAT_GCS_PREFIX = "heat-assessments/"
$env:REPORT_LOCAL_DIR = "reports"

cd backend
..\.venv\Scripts\python.exe main.py
```

You should see Flask listening on **[http://0.0.0.0:8080](http://0.0.0.0:8080)**.

Quick health check:

```powershell
Invoke-RestMethod http://localhost:8080/health
```

Expect something like `status=healthy` and `database=connected`.

`debug=False`, so **restart Flask** after Python changes (`report_pdf.py`, `report_wellness.py`, etc.) or form-generated PDFs will still be the old layout.

---



## 3b. Fastest PDF preview: notebook (no form submit)

Use this while iterating on page layout. Needs the **Cloud SQL proxy** (section 2). Flask is optional.

1. In Cursor, open `notebooks/pdf_page_preview.ipynb`.
2. Kernel: repo `.venv`  
   (`C:\Users\skgoa\OneDrive\Documents\Code\HEAT-Assessment-Dates\.venv\Scripts\python.exe`).
3. Run **Setup**, then **Load assessment**, then **Preview**.
4. Set `PAGE` in the Preview cell (`"wellness"`, `"mechanics"`, `"all"`, …) and re-run that cell after PDF code changes.

Jason Peele IDs in PlayerDev:

| ID | Date | Type | Wellness window |
|----|------|------|-----------------|
| 1 | 2025-11-23 | Initial | through Nov 23 (no WQ rows) |
| 2 | 2026-01-12 | 1st retest | Nov 23–Jan 12 (WQ starts Feb 8 → empty) |
| **3** | **2026-03-08** | **2nd retest** | **Jan 12–Mar 8 (use this)** |
| 4 | 2026-05-03 | 3rd retest | Mar 8–May 3 |

PNGs/PDFs land in `backend/reports/_preview/` (`page_wellness.pdf`, `page_mechanics.pdf`, …).

Set `CLEAR_CHART_CACHE = True` in the Preview cell if you changed `report_charts.py` or `report_wellness.py`.

---



## 4. Point the form at [localhost](http://localhost)

The published / default form talks to **Cloud Run**. For local API testing you must override that.

### Easiest: uncomment the local hook

In `[frontend/index.html](../frontend/index.html)`, find the block **above** the main `<script>`:

```html
<!-- LOCAL TESTING: uncomment the next line, then open this file in a browser.
<script>window.HEAT_API_BASE = 'http://localhost:8080';</script>
-->
```

Uncomment so it becomes:

```html
<script>window.HEAT_API_BASE = 'http://localhost:8080';</script>
```

Open the file in Chrome/Edge (double-click or drag into the browser):

`...\HEAT-Assessment-Dates\frontend\index.html`

**Re-comment that line before publishing** the form to GCS so trainers keep using production.

### Alternative: temporary edit of the default

Change the default `API_BASE` from the Cloud Run URL to `http://localhost:8080`, then revert before publish.

---



## 5. Smoke test checklist

Use a real player/date that has Blast / HitTrax / VALD data when you want charts filled in (e.g. Jason Peele on a known assessment day).

### A. Health + list

1. Form loads without the script dumping as text on the page.
2. Player autocomplete works (or type a known name).
3. Prior assessments appear for retest flow.



### B. Submit new assessment

1. Pick player, date, tools, optional notes / photos.
2. Submit.
3. Success message with assessment ID.
4. Browser should **start a PDF download** (if download endpoint is in your local code).
5. Check `backend\reports\` for a local PDF even if GCS is off.



### C. Regenerate

1. In **Regenerate PDF**, load by assessment ID or player + visit.
2. Notes / tools / images load.
3. Generate PDF → download starts again.
4. **PDF version history** lists versions (needs `assessment_report_versions` table for real v2+; otherwise you may see a single legacy entry).



### D. Download endpoint (optional curl)

```powershell
# Latest
Invoke-WebRequest "http://localhost:8080/api/hitting-assessment/3/report/download" -OutFile peele.pdf

# Specific version (after versions table + a regen)
Invoke-WebRequest "http://localhost:8080/api/hitting-assessment/3/report/download?version=1" -OutFile peele_v1.pdf
```

---



## 6. Common failures


| Symptom                       | Likely cause                                                                                     |
| ----------------------------- | ------------------------------------------------------------------------------------------------ |
| Form still hits Cloud Run     | Local `HEAT_API_BASE` script not active / page cached — hard refresh                             |
| `database` error / unhealthy  | Proxy not running, wrong `DB_*`, or firewall                                                     |
| CORS / failed fetch           | API not on 8080, or browser blocking `file://` → try a tiny static server (below)                |
| Empty metrics in PDF          | Wrong assessment **date**, tool unchecked, or no data that day for that name spelling            |
| GCS / AccessDenied            | Normal for raw bucket URLs; use **Download PDF** via API instead                                 |
| Version history empty / stuck | Run `deployment/assessment_report_versions.sql` on PlayerDev                                     |
| Script shows as text on page  | A `</script>` appeared inside a JS comment — don’t put script tags in comments inside `<script>` |




### If `file://` fetch is blocked

Serve the frontend over HTTP:

```powershell
cd C:\Users\skgoa\OneDrive\Documents\Code\HEAT-Assessment-Dates\frontend
python -m http.server 5500
```

Open [http://localhost:5500/index.html](http://localhost:5500/index.html) (with local `HEAT_API_BASE` set).

---



## 7. Stop when done

1. Ctrl+C the Flask process.
2. Ctrl+C the Cloud SQL proxy.
3. Re-comment / revert the localhost `HEAT_API_BASE` override so you don’t accidentally publish a local-only form.

---



## Related files

- API entry: `backend/main.py`
- PDF pipeline: `backend/report_pipeline.py`
- Form: `frontend/index.html`
- Attachments SQL: `deployment/assessment_attachments.sql`
- Versions SQL: `deployment/assessment_report_versions.sql`

