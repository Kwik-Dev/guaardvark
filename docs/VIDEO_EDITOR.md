# Video Editor — How To Use

Guaardvark's **Video Editor** is a *music-driven, auto-editing* studio. You drop in
video clips (B-roll), pick one song as the master soundtrack, and the **Art Director**
arranges the visuals to the beat. It runs entirely locally.

> **The one rule that trips people up:** the workflow is centered around a **song**.
> **Plan** (and therefore **Render** / **Quick Render**) stay disabled until your Bin
> contains **at least one video clip AND one audio clip set as the master soundtrack.**

---

## 1. Access it

Open the **Video Editor** page in the web UI (the bin + arrangement studio). The
screen is a set of draggable/resizable cards:

| Card | Purpose |
|------|---------|
| **Media Library** | Browse your **Videos / Audios / Images** Documents and drag them into the Bin |
| **Preview** | Watch the rendered MP4 (or the first Bin clip) + the Plan/Render toolbar |
| **Options** | Per-clip controls — master-song toggle/volume (audio) or Director's Notes (video) |
| **Bin** | The clips for *this* project — your working material |
| **Arrangement** | The timeline the Art Director built after Plan |

---

## 2. The workflow (step by step)

### Step 1 — Add video clips to the Bin
In the **Media Library** card, go to the **Videos** tab and click (or drag) the video
clips you want into the **Bin**.
- Any number of video clips works (≥1 is required).
- More clips let the editor build a richer montage.

### Step 2 — Add a song and set it as the master soundtrack
1. In the **Media Library** → **Audio** tab, add an audio file to the Bin.
2. **Select that audio clip** in the Bin.
3. In the **Options** card, toggle **"Use as master soundtrack"** ON.

> Only ONE clip can be the master song at a time — turning one on clears the others.
> The master song's beats/sections drive where the editor cuts the visuals.

### Step 3 — Plan
Hit **Plan** (it lights up once you have ≥1 video clip + a master song).
- The **Art Director** analyzes each clip (vision sampling — cached, so re-running is cheap)
  and builds an **arrangement** (per-clip filters, transitions, kept ranges).
- Watch progress in the toolbar; the finished **arrangement** appears in the
  **Arrangement** card.

### Step 4 — Render
Once Plan succeeds, **Render** lights up. Hit it to produce:
- a **`.mlt`** (editable Shotcut/MLT timeline), and
- a **`.mp4`** (the finished film).

The output streams into the **Preview** card.

### Optional — Quick Render
**Quick Render** = **Plan + Render** in one click. Set it and walk away — it
auto-chains into Render the moment the Plan lands.

---

## 3. Why the buttons are disabled

The action buttons are gated by:

```js
canPlan = (video clips in Bin ≥ 1) && (a master song is set)
```

| Button | Disabled when | How to enable |
|--------|---------------|---------------|
| **Plan** | No video clip **or** no master song in the Bin | Add ≥1 video clip **and** set an audio clip as master song |
| **Quick Render** | Same as Plan | Same as Plan |
| **Render** | No successful Plan yet (`planJob.result` is empty) | Run **Plan** first and let it finish |

If all three are greyed out, the almost-certain cause is that there's no **master
song**. A single video clip with no song is not enough — the editor has nothing to
edit the visuals against.

---

## 4. Fine-tuning

### Audio clip controls (Options card while an audio clip is selected)
- **Use as master soundtrack** — the one clip that drives the edit.
- **Volume** slider — mix level for the song.

### Video clip controls (Director's Notes)
- The Art Director writes per-clip analysis; you can **override** its decisions
  (these are applied on the next Plan).
- **Re-analyze** a single clip — drops the cache, re-samples frames, and runs a
  fresh vision pass (useful if you edited the source).

### Scan mode & style
- **Scan mode** and **Style recipe** sit in the Options card. Changing either
  invalidates the current plan (you'll re-Plan).

### Text overlays
- Add text elements and drag them on the **Preview**; they render over the video.

### Open in Shotcut
- After a render, the **Shotcut** button appears — it launches the `.mlt` project in
  Shotcut for manual refinement.

---

## 5. Projects & saving

- **New / Open / Save / Save As / Rename** live in the project bar.
- Named projects persist per-project on the backend (the card layout is stored
  separately as global UI state).
- Edits are **autosaved** as drafts continuously; a **Saved** / **Saving…** chip shows
  the state.

---

## 6. Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Space` | Play / pause the preview |
| `Delete` / `Backspace` | Delete the selected text overlay |
| `Cmd/Ctrl + Z` | Undo the last timeline edit |

*(Skips when you're typing in an input/textarea.)*

---

## 7. Where the output lands

- `.mlt` and `.mp4` are written to `data/outputs/videos/editor-renders/` and registered
  as Documents you can reopen from the Media Library / Documents page.

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| **Plan / Quick Render disabled** | No master song set, or no video clip in the Bin. Add a song and toggle **"Use as master soundtrack"**. |
| **Render disabled** | Plan hasn't succeeded yet. Run Plan first. |
| **"Hit Plan first — no arrangement to render yet"** | The Bin or a saved project has no arrangement. Re-run Plan. |
| **Media Library is empty** | Upload videos/audio/images to Documents first (or use output from the Film Crew / batch video generator). |
| **Rendering fails / preview won't play** | Confirm the source Documents still exist and re-run the clip's **Re-analyze**; check the Video Editor backend is healthy. |

---

## 9. The model in one line

> **video clips (visuals) + one master song (drive the cuts) → Plan (Art Director
> builds the arrangement) → Render (.mlt + .mp4) → refine in Shotcut if needed.**
