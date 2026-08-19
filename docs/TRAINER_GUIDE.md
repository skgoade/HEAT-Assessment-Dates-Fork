# Hitting Assessment Tool — Trainer Guide

How to use the RBI HEAT Hitting Assessment form to create and refresh player PDFs.

Open the form in your browser (bookmark the URL your team uses). You do **not** need to touch the database or code.

---

## What this tool does

1. You choose **Submit a new assessment** or **Regenerate an existing PDF**.
2. You move through one section at a time with **Next** / **Previous** (player, tools, photos, notes).
3. On submit, the system pulls that day’s **Blast**, **HitTrax**, and **VALD** data (only for tools you checked), builds a draft PDF, and saves it.
4. You can **regenerate** a PDF later to fix notes, tools, video link, or images without creating a new assessment.

Metrics are tied to the **assessment calendar day** (that date only), not a ±7-day window.

Use **Change** at the top if you picked the wrong path. Your entries stay filled.

---

## New assessment

Choose **Submit a new assessment**, then step through the sections.

### 1. Athlete — player name
- Type the player’s name. A roster list narrows as you type (player directory first, then names from prior assessments). You can pick a match or keep typing a name that is not listed.
- If they’ve been assessed before, the form shows prior visits so you can pick Initial vs Retest and which previous assessment to compare against.
- A retest **carries Training Focus** and any open **Development** phase badges from the last visit; edit them before submit.
- A peek under the name shows **athlete, assessment number** (Initial / 1st Retest / …), **height, weight, and age** — the same header fields as page 1 of the PDF. Age uses the assessment date below. If they are not in the directory, height / weight / age print as —.

### 2. Athlete — assessment date
- Use the date the session actually happened. Charts and tables pull data for that day. Age in the peek (and on the PDF) is calculated on this date.

Optional: trainer name. Click **Next**.

### 3. Session — tools used
The form looks up that player’s Blast / HitTrax / VALD rows for the assessment date and shows a short count under each checkbox (for example `38 contacts · peak EV 98.4 mph`, or `No tests found for this date`). On a **new** assessment it unchecks tools with no data; you can still check them if you want those pages anyway.

| Tool | If unchecked… |
|------|----------------|
| **Blast Motion** | Blast table and related visuals are hidden |
| **HitTrax** | HitTrax tables and batted-ball charts are hidden |
| **VALD** | VALD tables and ForceDecks charts are hidden |

Example: peek says no VALD that day → leave VALD unchecked so empty VALD pages don’t appear.

### 4. Session — video analysis link (optional)
- Paste a YouTube / Drive / similar URL.
- It shows as a clickable link near the top of the PDF.

### 5. Swing photos — mechanics phase cards (optional)
Each swing phase gets its own card:

1. Load phase  
2. Load position  
3. Stride phase  
4. Launch position  
5. Impact  

Per phase you can add:
- **Status** — Strength / Monitor / Development (green / amber / red badge on the PDF). On a retest, open **Development** badges from the last visit are pre-selected; Strength and Monitor are not carried.
- **Caption** (coach cues for that stage — shown under the photo)
- **One photo** per phase. PNG/JPEG/WebP, up to 4&nbsp;MB. Photos fill a tall phone-like portrait frame on the PDF. After you add a photo, **drag** to reframe and use the **zoom** slider if the automatic crop cuts off the athlete. **Reset** restores the centered crop.

Cards appear in a left-to-right sequence on PDF page 1 (Hitting Assessment).

### 6. Extra images (optional)
Screenshots and other context images. Use ↑ ↓ to set PDF order. Captions are recommended. Form checkboxes still say Blast / HitTrax / VALD so you can hide empty tool pages; those brand names are not printed on the PDF.

### 7. Notes (optional)
These sit under the swing sequence. Empty boxes are omitted, and the remaining cards fill that space:

- **Mechanical Observation** — left card on the two-up layout.
- **Training Focus** — right card. On a retest this is pre-filled from the last visit.
- **Assessment Notes** — shorter strip under those two cards when all three are filled.

Layout:
- **One filled** — that card uses the full width and height of the notes area.
- **Two filled** — both cards are full width and stacked.
- **All three filled** — Mechanical Observation and Training Focus sit side by side, with Assessment Notes in a shorter strip below.

Use the formatting bar on each box (**B**, *I*, Heading, Body, List) instead of typing markdown. Ctrl/Cmd+B and Ctrl/Cmd+I also work.

- **Best of Day Summary** — one note for jump / hop / pull tests (later page).

### 8. Submit
- On the last section, click **Submit Assessment**.
- Success message includes the **assessment ID** (save it) and a download when ready.
- You can regenerate from that success message, or **Start another** to return to the first screen.

---

## Regenerate PDF

Choose **Regenerate an existing PDF** when notes, tools, video link, or images need updating after the first draft. You cannot continue past **Find assessment** until an assessment is selected and loaded.

### Find the assessment
Pick one mode:

**A) Assessment ID**  
Enter the ID from the success message (e.g. `4`). After it loads, the form peeks the player directory for that athlete.

**B) Player name + visit**  
Type to search the roster, then choose Initial / 1st Retest / 2nd Retest / etc. from the dropdown. The directory peek shows athlete + assessment number as soon as the name matches, even before a visit is chosen.

Once the assessment is identified, the form **automatically loads**:

- Existing notes  
- Tools used  
- Video analysis link (if any)  
- Phase photos into each mechanics card (preview + caption + Remove)  
- Extra images into the extra-visual rows (preview + caption + Remove)  

No need to click Load first (there is still a **Reload saved images** button on the Images step if you want to discard local edits and refetch). Then **Next** through Session, Images, and Notes.

### Edit before generating
- Change notes, tools, or video link as needed. Session still shows a data peek so you can uncheck tools with no rows that day.
- **Remove a photo:** use **Remove photo** on that phase card (or × on an extra image), then generate.
- **Replace a photo:** choose a new file on that card; the old one is removed when you generate.
- **Reframe a photo:** drag in the portrait well and use zoom; **Reset** restores the centered crop. Generate to apply it to the PDF.
- **Captions:** edit them on the card / extra row (they grow with the text). Extra-image order uses ↑ ↓.

### Generate
On the last section, click **Generate PDF**. The PDF is rebuilt and uploaded as a **new version** (older PDFs stay available under **PDF version history**). Your browser should **download the PDF automatically**; you can also download any prior version from the history list.

If player + visit lookup finds nothing, use **Change** and start a **new** assessment instead.

---

## What’s in the PDF (high level)

| Section | Source |
|---------|--------|
| Page 1 — Hitting Assessment header + swing sequence + Mechanical Observation / Training Focus / Assessment Notes | Form + `player_directory` + that day’s handedness; phase photos/status/captions + the three page-1 cards |
| Batted Ball Profile (4 KPI cards + table) | That day’s batted-ball totals; ▲▼ vs previous |
| Swing Metrics | Blast by bat (Game / Handle / Barrel / Under), side-by-side |
| Batted ball by location | Location table, then EV/LA zone heatmaps side by side |
| Contact location | Plate (vertical + depth) and a smaller POI zone chart |
| Flight & spray | Spray (labeled distance arcs) + EV×LA (dual y) |
| Best of Day Metrics | Jump / hop / pull KPI cards + one optional summary; trend cards sit with that block |
| Extra images | Your uploads + captions |
| Wellness & Readiness (**retests only**, last page) | Self-reported check-ins for that training block |

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
- To replace a bad photo: open **Regenerate**, go to **Images**, choose a new file on that card (or Remove, then add a file), then Generate PDF.
- Retest labels (1st / 2nd / 3rd…) are automatic from that player’s history.
- Use **PDF version history** on the regenerate section to download an older draft after a later regenerate.

---

## Need help?

- Empty metrics for a tool → confirm the tool checkbox, the assessment **date**, and that data exists for that player that day.
- Form or PDF errors → note the assessment ID and what you clicked, and contact whoever maintains the HEAT assessment backend.
- Download fails → confirm you’re on the published form (points at `heat-assessment-api`) and try the **Download PDF** button again.

For engineering setup (database, deploy, API), see [QUICKSTART.md](QUICKSTART.md), [DEVELOPER_OPS.md](DEVELOPER_OPS.md), and the repo [README](../README.md).
