# Promo video from a followup folder (Image-to-Video → Video Editor)

This is the recommended way to turn a client's followup images into a finished promo
video. The Video Editor is built around **video** clips, so the first step is to animate
your still images into `.mp4` clips with **Image-to-Video**, then assemble those clips
(plus music) in the editor.

> **Why I2V first?** The Video Editor's **Plan** pipeline only arranges clips of kind
> `video`. Still images dropped from Finder are tagged `image` and are ignored by Plan.
> Converting them to `.mp4` first makes them first-class clips and avoids the
> image-kind workaround.

## Step 1 — Register the client
**Page:** `/clients` → **"Add New Client"** → the **Client Action Modal**.

Fill the basic fields:
| Field | Value |
|---|---|
| **Client Name** | `Kwiksher` |
| **Email Address** | `support@kwiksher.com` |
| **Phone Number** | *(leave blank)* |
| **Location** | `Florida, USA` |
| **Notes** | `Interactive book publishing plugin for Photoshop. No-code, publish to iOS & Android.` |

Open the **"RAG Training Data (Optional)"** accordion and fill:
- **Industry** → add `Software`, `Digital Publishing`
- **Keywords** → add `Photoshop plugin`, `interactive books`, `iOS`, `Android`
- **Unique Selling Points** → add `No-code interactive books`, `Publish to iOS & Android`
- **Content Goals** → add `Promotion video`, `Brand awareness`
- **Brand Voice Examples** → `"Creative, user-friendly, empowering — 'You are an artist, not a code jockey.'"`

Click **Save**.

## Step 2 — Generate the music track
**Page:** `/audio` (**Audio Foundry**) → **"Music"** tab.

1. **Genre** chips → select `Pop`.
2. **Mood** chips → select `Playful`, `Uplifting`, `Whimsical`.
3. **Instruments** chips → select `Piano`, `Strings`, `Bells`.
4. **Additional details** (free text) → paste a fun, storybook prompt:
   > `"whimsical and playful, like a storybook come to life, twinkling music-box bells, bouncy piano, gentle strings, magical and joyful, light and airy, feels like turning the pages of a children's picture book"`
5. **Duration** slider → `30` seconds.
6. **Instrumental only** → ON.
7. **Polish with AI** → ON, click **"Polish & Preview"** to refine, then **"Compose Music"**.
8. **Download** the `.mp3`/`.wav` — your soundtrack for Step 4.

## Step 3 — Animate the followup images into clips (Image-to-Video)
**Page:** `/video` (**Video Generator**).

1. Switch the **input mode** to **Image** (I2V).
2. **Drag & drop** the followup images into the **"Drag & drop images here, or click to upload"** box:
   ```
   data/clients/followup/kwiksher/video/images/img_0.png … img_26.jpg
   data/clients/followup/kwiksher/assets/logo.png
   ```
3. In **"Describe the motion or action (optional)"**, tell it how each should move, e.g.:
   > `"slow camera zoom in, gentle parallax, pages of the interactive book flipping, subtle logo glow"`
4. Pick an **I2V model** (e.g. `Wan 2.2 5B TI2V`).
5. **Generate** → each still becomes an animated `.mp4` clip.

## Step 4 — Assemble the clips in the Video Editor
**Page:** `/video-editor` (**Video Editor**).

> The Media Library has **no upload button** — media is added by **dragging files from
> Finder** onto the editor, or by **clicking** items already in the Media Library.

1. **Drop the `.mp4` clips** (from Step 3) onto the editor → they land in the **Bin** as
   `video` clips. (Drop the `.mp3`/`.wav` from Step 2 in the same drag — it routes to
   the **Audio** folder.)
2. **Refresh the page** so the Media Library picks up the new files.
3. **Set the master soundtrack:** select the audio clip in the Bin → check
   **"Master soundtrack"** in the **OptionsPanel** (right side).
4. **Plan** → the Art Director auto-arranges the timeline (order, timing, transitions).
5. Review the **Arrangement Preview**.
6. **Render** (or **Quick Render**) → produces the final `.mp4`.

## Step 5 — Text overlays (optional)
**Page:** `/video-text-overlay` → add tagline/CTA text using brand colors `#003388`
(blue) / `#ffa902` (orange).

**Suggested on-screen text:**
- Opening: `"Create interactive books — no code."`
- Middle: `"Publish to iOS & Android."`
- End card: `"Change how you experience stories."` + `kwiksher.com`

## Suggested clip order (from your 27 images)
1. **Hero / product shot** (open on the brand)
2. **Interface / Photoshop plugin** (show the tool)
3. **Portfolio pieces** (Kappa Jizo, Santa Music Box, Eka, Match Puzzle — show the results)
4. **Logo + tagline** (end card)

---

# Video Generator configurations (`/video`)

The Video Generator turns stills into clips (Image-to-Video) or text into clips
(Text-to-Video). Each uploaded image becomes its **own** clip of the selected duration.

## Input mode
- **Text** (T2V) — generate a clip from a prompt.
- **Image** (I2V) — animate an uploaded still (drag & drop, or click to upload).

## Model
- **Wan 2.2** (recommended, MPS-friendly) — motion comes from the **prompt** only.
- **LTX-2.3 / 2.5** — fast, distilled.
- **CogVideoX 5B** — uses `motion_bucket_id`, so the **Motion** preset actually works here.

## Duration presets (per image, by model)

**Wan 2.2:**
| Preset | Per-image duration | Frames @ 16fps |
|---|---|---|
| Short | ~2 s | 33 |
| Medium | ~3 s | 49 |
| Long | ~5 s | 81 |

**LTX-2.3 / 2.5:**
| Preset | Per-image duration |
|---|---|
| Short | ~4 s |
| Medium | ~6 s |
| Long | ~10 s |

**CogVideoX 5B:**
| Preset | Per-image duration |
|---|---|
| Short | ~3 s |
| Medium | ~4 s |
| Long | ~6 s |

> Duration is **per image**, not the total. With 12 images at **Long** (5s each) you get
> ~60s of clips.

## Other configurations
- **Aspect Ratio** — `16:9` (widescreen), `9:16` (portrait/Reels), `1:1` (square), etc.
- **Video Size** — resolution (pinned/disabled for LTX; selectable for Wan/CogVideoX).
- **Quality** — ⚡ Fast (10 steps) / ✨ Standard (30) / 🎬 High (40) / 🏆 Maximum (50).
  More steps = sharper but slower.
- **Motion** — 🌊 Subtle / 🎯 Normal / 💨 Dynamic / 🔥 Intense.
  > ⚠️ **No-op for Wan 2.2 I2V.** The Motion preset only affects models that use
  > `motion_bucket_id` (CogVideoX / SVD). On Wan I2V, motion is controlled **entirely by
  > the prompt** — changing this dropdown does nothing.
- **Output Quality** — Draft / Standard (2× FPS interpolation) / **Cinema** (2× FPS +
  2× upscale, recommended for final output).
- **Prompt Style** — e.g. `cinematic`.
- **Enhance Prompt** — toggle; rewrites the prompt via the backend enhancer.
- **Fidelity Mode (Exact text mode)** — uses **light enhancement only** (orientation +
  motion hints, no heavy style boilerplate). Prevents garbling of on-screen text/logos
  and preserves the source image. **Turn this ON to keep a thumbnail undistorted and
  color-true.**
- **Director Mode** — rewrite each prompt via the cinematic Director.
- **Cinematic Keyframe** — FLUX still → Wan I2V per clip (slower, sharper).
- **Storyboard Mode** — one concept → N director-written shots.
- **Look & Feel** — free-text style steer.
- **Negative Prompt** — free text of things to avoid.
- **Low VRAM mode** — clamps frames/steps/resolution when memory-constrained.
- **Advanced** — num_inference_steps, guidance_scale, seed, frames_per_batch, face_restore.

## Preserving a picture-book thumbnail (no distortion / no color shift)
1. **Fidelity Mode (Exact text mode)** → **ON** — this is the key setting; it stops the
   enhancer from adding heavy style that warps the image.
2. **Motion** → **Subtle** (or rely on the prompt; it's a no-op for Wan anyway).
3. **Duration** → **Short/Medium** — shorter clips drift less.
4. **Quality** → **High/Maximum** — more steps = more faithful to the source.
5. **Prompt** → ask for camera motion only and preserve the artwork:
   > `"keep the image exactly as shown, do not change the colors or details, only add a very gentle camera drift, subtle and still, preserve the original artwork"`

> **Honest caveat:** even with Fidelity Mode + a preservation prompt, Wan I2V can still
> subtly warp or shift colors on a detailed thumbnail. If the image must stay
> pixel-perfect, skip I2V and do a **pure camera zoom/pan in the Video Editor**
> (scale/transform keyframe) — that moves the camera without touching the artwork.

## Recommended for a picture-book promo
- **Duration** → **Long** (~5s each) for a slower, cinematic feel; **Medium** (~3s) for a
  snappier pace.
- **Motion** → **Subtle** or **Normal** (gentle camera drift suits picture books).
- **Output Quality** → **Cinema** for the final render.
- **Aspect Ratio** → match your target (16:9 for YouTube, 9:16 for Reels/Shorts).

> To get a single continuous video, assemble the per-image clips in the Video Editor
> afterward (or use `/music-video`).

---

