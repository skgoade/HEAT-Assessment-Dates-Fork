# Hitting Assessment Tool — Trainer Guide

How to use the RBI HEAT Hitting Assessment form to create and refresh player PDFs.

Open the form in your browser (bookmark the URL your team uses). You do **not** need to touch the database or code.

---

## What this tool does

1. You enter who was assessed, when, what tools you used, notes, photos, and an optional video link.
2. On submit, the system pulls that day’s **Blast**, **HitTrax**, and **VALD** data (only for tools you checked), builds a draft PDF, and saves it.
3. You can **regenerate** a PDF later to fix notes, tools, video link, or images without creating a new assessment.

Metrics are tied to the **assessment calendar day** (that date only), not a ±7-day window.

---

## New assessment (top of the form)

### 1. Player name
- Type the player’s full name (autocomplete may suggest prior names).
- If they’ve been assessed before, the form shows prior visits so you can pick Initial vs Retest and which previous assessment to compare against.

### 2. Assessment date
- Use the date the session actually happened. Charts and tables pull data for that day.

### 3. Tools used
Check only what you used that day:

| Tool | If unchecked… |
|------|----------------|
| **Blast Motion** | Blast table and related visuals are hidden |
| **HitTrax** | HitTrax tables and batted-ball charts are hidden |
| **VALD** | VALD tables and ForceDecks charts are hidden |

Example: no VALD that day → uncheck VALD so empty VALD pages don’t appear.

### 4. Video analysis link (optional)
- Paste a YouTube / Drive / similar URL.
- It shows as a clickable link near the top of the PDF.

### 5. Page 1 notes (optional)
- **Mechanical Observation** — left card under the swing sequence.
- **Training Focus** — right card; bullets (`- item`) work well.
- **Assessment Notes** — shorter strip under those two cards.

Empty cards still print so the layout stays even. Light formatting is supported: `**bold**`, `*italic*`, `` `code` ``, `- ` bullets, numbered lists, and `# ` headings.

- **Best of Day Summary** — one note for jump / hop / pull tests (later page). Leave blank to omit.

### 6. Mechanics phase cards (optional)
Each swing phase gets its own card:

1. Load phase  
2. Load position  
3. Stride phase  
4. Launch position  
5. Impact  

Per phase you can add:
- **Status** — Strength / Monitor / Development (green / amber / red badge on the PDF)
- **Caption** (coach cues for that stage — shown under the photo)
- **One photo** per phase. PNG/JPEG/WebP, up to 4&nbsp;MB. The PDF shows each photo in the same portrait frame (center-cropped), so landscape or odd-sized uploads still line up.

Cards appear in a left-to-right sequence on PDF page 1 (Swing Mechanics).

### 7. Extra visuals (optional)
Screenshots and other context images. Use ↑ ↓ to set PDF order. Captions are recommended. Form checkboxes still say Blast / HitTrax / VALD so you can hide empty tool pages; those brand names are not printed on the PDF.

### 8. Submit
- Click **Submit Assessment**.
- Success message includes the **assessment ID** (save it) and a link to the PDF when ready.
- You can regenerate from that success message if needed.

---

## Regenerate PDF (bottom of the form)

Use this when notes, tools, video link, or images need updating after the first draft.

### Find the assessment
Pick one mode:

**A) Assessment ID**  
Enter the ID from the success message (e.g. `4`).

**B) Player name + visit**  
Enter the player, then choose Initial / 1st Retest / 2nd Retest / etc. from the dropdown.

Once the assessment is identified, the form **automatically loads**:

- Existing notes  
- Tools used  
- Video analysis link (if any)  
- Images already attached  

No need to click Load first (there is still a **Reload from saved assessment** button if you want to discard local edits and refetch).

### Edit before generating
- Change notes, tools, or video link as needed.
- **Delete images:** check the ones to remove, then generate — they are deleted when you click Generate PDF.
- **Reorder phase photos:** use ↑ ↓ in **Images already attached** (within that phase). The PDF uses the first photo per phase.
- **Add images:** one new photo per phase (remove an existing phase photo first if replacing). Extra visuals work the same as on a new submit.

### Generate
Click **Generate PDF**. The PDF is rebuilt and uploaded as a **new version** (older PDFs stay available under **PDF version history**). Your browser should **download the PDF automatically**; you can also download any prior version from the history list.

If player + visit lookup finds nothing, create a **new** assessment at the top of the form instead.

---

## What’s in the PDF (high level)

| Section | Source |
|---------|--------|
| Page 1 header (athlete, date, type, trainer, video, height, weight, age, handedness) | Form + `player_directory` + that day’s handedness |
| Swing sequence + Mechanical Observation / Training Focus / Assessment Notes | Phase photos/status/captions + the three page-1 cards |
| Batted Ball Profile (4 KPI cards + table) | That day’s batted-ball totals; ▲▼ vs previous |
| Swing Metrics | Blast by bat (Game / Handle / Barrel / Under), side-by-side |
| Batted ball by location | Location table, then EV/LA zone heatmaps side by side |
| Contact location | Plate (vertical + depth) and a smaller POI zone chart |
| Flight & spray | Spray (labeled distance arcs) + EV×LA (dual y) |
| Best of Day Metrics | Jump / hop / pull KPI cards + one optional summary; trend cards sit with that block |
| Extra images | Your uploads + captions |

The PDF does **not** print HitTrax / Blast / VALD brand names. Form tool checkboxes still use those names so you can omit empty pages.

**Blast by bat:** swings are grouped from Blast `equipment_name` / nickname into **Game Bat**, **Handle Load**, **Barrel Load**, and **Under Load** (substring match; anything else counts as Game Bat). The PDF shows one side-by-side Swing Metrics table so all bats line up for comparison (current date + Δ previous + Δ initial under each bat).

**EV × LA:** launch angle on the x-axis; exit velocity (left) and distance (right). Circles are EV by flight type; diamonds are distance. No predicted-carry / peak-LA overlay yet — that waits on a larger-sample model.

**Spray:** batted balls on the field, colored by EV. Labeled arcs are distance from home (ft).

**PDF downloads:** after submit/regenerate, the form downloads the PDF through the API (not a public GCS link). Use **PDF version history** to download older drafts.

**Previous vs baseline:** if they are the same visit (e.g. first retest), the PDF shows only **Previous** so the comparison isn’t duplicated.

---

## Tips

- Match the **player name spelling** to how Blast / HitTrax / VALD store the athlete, or metrics may be empty.
- Prefer **captions** on photos the first time so you don’t need a cleanup regen.
- To replace a bad photo: check it under “Images already attached,” add the new file, then Generate PDF.
- Retest labels (1st / 2nd / 3rd…) are automatic from that player’s history.
- Use **PDF version history** on the regenerate section to download an older draft after a later regenerate.

---

## Need help?

- Empty metrics for a tool → confirm the tool checkbox, the assessment **date**, and that data exists for that player that day.
- Form or PDF errors → note the assessment ID and what you clicked, and contact whoever maintains the HEAT assessment backend.
- Download fails → confirm you’re on the published form (points at `heat-assessment-api`) and try the **Download PDF** button again.

For engineering setup (database, deploy, API), see [QUICKSTART.md](QUICKSTART.md), [DEVELOPER_OPS.md](DEVELOPER_OPS.md), and the repo [README](../README.md).
