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

### 5. Assessment notes (optional)
- Free-text coach notes. They print at the **end** of the PDF.
- Light formatting is supported:
  - `**bold**`, `*italic*`, `` `code` ``
  - Bullet lists with `- ` or `* `
  - Numbered lists with `1. `
  - Headings with `# ` / `## `

### 6. Mechanics photos (optional)
Upload the five phase shots when you have them:

1. Load phase  
2. Load position  
3. Stride phase  
4. Launch position  
5. Impact  

Add a short caption under each (these become labels in the PDF). All five are laid out on one mechanics page when present.

### 7. Extra visuals (optional)
Screenshots (HitTrax exports, VALD cards, etc.). Use ↑ ↓ to set PDF order. Captions are recommended.

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
- **Add images:** use the mechanics / extra visual rows the same way as on a new submit.

### Generate
Click **Generate PDF**. The PDF is rebuilt and uploaded as a **new version** (older PDFs stay available under **PDF version history**). The success message includes a signed link to open the latest draft.

If player + visit lookup finds nothing, create a **new** assessment at the top of the form instead.

---

## What’s in the PDF (high level)

| Section | Source |
|---------|--------|
| Header (player, visit type, video link) | Form + assessment record |
| Current snapshot / HitTrax / Blast / VALD tables | That day’s data for checked tools; Δ vs previous/baseline when available |
| Batted ball visuals | Auto from HitTrax (zones, contact location, EV×LA, spray) |
| VALD charts + metric definitions | Auto from VALD when enabled |
| Mechanics + extra images | Your uploads + captions |
| Assessment notes | Your notes (end of report) |

**EV × LA “Optimal LA”:** based on a hang-time style proxy from the session’s EV vs launch-angle trend (often near ~80° when EV only falls gently with LA). It is not peak carry distance. *(Pending Noah sign-off on this rule vs a different optimal-LA definition.)*

**Previous vs baseline:** if they are the same visit (e.g. first retest), the PDF shows only **Previous** so the comparison isn’t duplicated.

**PDF links:** reports live in a private Google Cloud bucket. Open links from the form (they are time-limited). Opening a raw `storage.googleapis.com` URL in a new tab without a signature will show Access Denied.

---

## Tips

- Match the **player name spelling** to how Blast / HitTrax / VALD store the athlete, or metrics may be empty.
- Prefer **captions** on photos the first time so you don’t need a cleanup regen.
- To replace a bad photo: check it under “Images already attached,” add the new file, then Generate PDF.
- Retest labels (1st / 2nd / 3rd…) are automatic from that player’s history.
- Use **PDF version history** on the regenerate section to reopen an older draft after a later regenerate.

---

## Need help?

- Empty metrics for a tool → confirm the tool checkbox, the assessment **date**, and that data exists for that player that day.
- Form or PDF errors → note the assessment ID and what you clicked, and contact whoever maintains the HEAT assessment backend.
- Access Denied on a PDF → reload the assessment in the form and use the new Open link (or ask eng to check signed-URL IAM — `docs/NOAH_OPS.md`).

For engineering setup (database, deploy, API), see [QUICKSTART.md](QUICKSTART.md), [NOAH_OPS.md](NOAH_OPS.md), and the repo [README](../README.md).
