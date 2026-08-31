# Clients Guide

How to create, register, and enrich Guaardvark clients — from the web UI form, to bulk
import from CSV/Markdown, to gathering website assets and promotion-video kits.

## 1. Client fields

A client is created via `POST /api/clients/` (`backend/api/clients_api.py`), backed by
the `Client` model (`backend/models.py`). The web form is
`frontend/src/components/modals/ClientActionModal.jsx`. Only **`name` is required**;
everything else is optional.

### Basic fields

| Field | Type | Notes |
|---|---|---|
| `name` | string | **Required**, unique. |
| `email` | string | Unique; validated for format. |
| `phone` | string | Contact phone. |
| `location` | string | e.g. "Miami, FL". |
| `logo_path` | string | Set via the logo upload endpoint, not the form. |
| `notes` | string | Free-form notes. |

### RAG Training Data (the collapsible "RAG Training Data (Optional)" accordion)

| Field | Type | UI | Purpose |
|---|---|---|---|
| `industry` | array | chip input | Industry/market classification (e.g. Healthcare, Legal). |
| `geographic_coverage` | array | chip input | Service areas (cities/states/zips). |
| `target_audience` | array | autocomplete + free text | Who the client's target customer is. |
| `unique_selling_points` | array | autocomplete + free text | Key differentiators/value props. |
| `content_goals` | array | autocomplete + free text | Marketing objectives (SEO, Lead Gen, etc.). |
| `brand_voice_examples` | string | multiline text | Sample content showing desired tone/voice. |
| `regulatory_constraints` | string | multiline text | Compliance reqs (HIPAA, GDPR, FDA…). |
| `keywords` | array | chip input | SEO keywords for content generation. |
| `competitor_urls` | array | chip input | Competitor websites for analysis. |

Array fields are stored as JSON in the DB and accepted as JSON arrays by the API.

> Note: the CLI's `guaardvark clients create` only supports `name` + `description` — it
> does **not** expose the RAG fields. For full field support use the web UI or the bulk
> importer below.

## 2. Bulk registration — `scripts/import_clients.py`

Reads a CSV or Markdown file and POSTs each record to `/api/clients/`. Handles the
array fields (comma- or `|`-separated), sends `X-API-Key` if `GUAARDVARK_API_KEY` /
`LLX_API_KEY` is set, and supports `--dry-run`.

```bash
# Validate first (no writes)
python3 scripts/import_clients.py clients.csv --dry-run

# Actually register
python3 scripts/import_clients.py clients.csv
python3 scripts/import_clients.py clients.md --server http://localhost:5000
```

**CSV** — header row = field names, one client per row; array fields use `|` or `,`:

```csv
name,email,phone,location,industry,keywords,content_goals
Acme Corp,hello@acme.com,+1-555-0100,Miami FL,Healthcare|Legal,telehealth|HIPAA,Lead Generation
```

**Markdown** — one `---`-delimited YAML block per client:

```md
---
name: Acme Corp
email: hello@acme.com
industry: Healthcare, Legal
keywords: telehealth, HIPAA
brand_voice_examples: |
    We speak plainly and put patients first.
---
```

The script skips records without a `name`, reports per-record success/failure (e.g. 409
on duplicate name/email), and exits non-zero if any failed.

## 3. Followup folder — `--followup`

Add a `website` field to a record (it is **not** sent to the API; it's followup-only),
then run with `--followup` to download the site's logo/favicon/hero and save analysis
data into a per-client folder:

```bash
python3 scripts/import_clients.py clients.md --followup --dry-run   # preview
python3 scripts/import_clients.py clients.md --followup             # register + assets
```

```md
---
name: Kwiksher
website: https://kwiksher.com/
---
```

Creates:

```
data/clients/followup/<slug>/
├── client.md            # the import file + a "## Followup assets" section
│                        #   referencing assets/, data/, video/ (when present)
├── assets/             # logo, favicon, og-image, hero (downloaded)
└── data/analysis.json  # extracted site data + asset URLs
```

`<slug>` = lowercased, non-alphanumeric → `-` (e.g. `Kwiksher` → `kwiksher`).

`client.md` is the import file with a `## Followup assets` section appended that lists
the **actual files** gathered, with paths relative to the folder. Image files are
**embedded as markdown images** (viewable in a markdown preview), e.g.
`![logo.png](assets/logo.png)`; non-images (JSON, video) are listed as code paths
(e.g. `data/analysis.json`, `video/videos/video_0.mp4`).

## 4. Promotion-video kit — `--video`

Add `--video` to also gather a full promo-video kit into `<slug>/video/`:

```bash
python3 scripts/import_clients.py clients.md --video --dry-run   # preview
python3 scripts/import_clients.py clients.md --video             # register + full kit
```

```
<slug>/video/
├── images/            # all page images: product shots, screenshots, portfolio, banners
├── videos/            # any <video>/<source> or .mp4/.webm/.mov found on the site
└── video_kit.json     # structured data for the edit:
    ├── meta           # title, og:title/description/image/site_name
    ├── brand.colors   # hex palette + dominant color (from page CSS)
    ├── messaging.testimonials  # blockquote quotes (voiceover / on-screen text)
    ├── socials        # facebook / x / instagram / linkedin / youtube / tiktok links
    └── assets         # image + video URLs actually downloaded
```

**Using the kit for a promo video:**
- **B-roll / stills:** `video/images/` (product shots, screenshots, portfolio).
- **Footage:** `video/videos/` if the site hosts any.
- **Branding:** logo from `assets/logo.png` + color palette from
  `video_kit.json` → `brand.colors` for lower-thirds, titles, and background.
- **Script / voiceover:** `messaging.testimonials` + the client record's
  `unique_selling_points`, `content_goals`, `brand_voice_examples`.
- **End card / CTA:** `socials` + the client's `website` / `email`.

## 5. Asset overrides

The auto-picker is good but not perfect. Force specific assets with global flags:

```bash
python3 scripts/import_clients.py clients.md --video \
  --logo "https://example.com/logo-blue.png" \
  --hero "https://example.com/hero.jpg" \
  --video-url "https://example.com/demo.mp4" \
  --dry-run
```

- `--logo <url>` — override the auto-detected logo.
- `--hero <url>` — override the auto-detected hero image.
- `--video-url <url>` — download an explicit video into the kit (repeatable, or
  comma-separated), in addition to any auto-detected ones.

## 6. Agent skill — `client-from-website`

The pi skill `client-from-website` automates the whole flow from a website URL: fetch →
extract fields → write the client `.md` → create the followup folder → gather the video
kit. It lives at `~/.pi/agent/skills/client-from-website/SKILL.md` and is invoked by
asking to "create a client from this website URL".

## Gotchas

- `brand.colors.dominant` is the most common hex (often white); the *real* brand colors
  are usually the saturated entries in the palette.
- If a site lazy-loads videos, they may not appear in `video/videos/` — check the page
  source or use browser tools to find the real `.mp4` URL.
- The importer only accepts the documented fields; extra keys are dropped silently.

----

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

# FFmpeg still video (stills → camera-motion clips)

For images that must stay **pixel-perfect** (picture-book thumbnails, logos,
infographics), use **FFmpeg** instead of I2V. FFmpeg moves only the *camera* — it
never re-renders the artwork, so there is **zero distortion and zero color change**.

**Three patterns:**
| Key | UI label | Behavior |
|---|---|---|
| `static` | **A · Static** | Holds the frame — no movement, pixel-perfect. |
| `ken_burns_zoom` | **B · Zoom** | Slow camera push-in (zoom). |
| `ken_burns_pan` | **C · Pan** | Slow left-to-right camera pan. |

**Web UI (`/video`):** click the **FFmpeg** toggle (🎬 icon, next to Image) → upload
images → pick a pattern (A/B/C), duration (2–10s), FPS (24/25/30), resolution
(480p/720p/1080p) → **Generate FFmpeg Clips**. Results return instantly; each clip is
saved to the **Media Library** (Videos) with a Download link.

**CLI:**
```bash
python scripts/ffmpeg_stills.py images/*.png --pattern ken_burns_zoom --duration 5 \
  --width 1280 --height 720
```

**Full reference:** see `docs/FFMPEG_STILL_VIDEO.md`. Generated clips are `video` clips,
so they work directly in the Video Editor's **Plan** pipeline.

---

# Audio prompt options (children's book brand)

## Option 1 — Playful & whimsical (best fit)
**Genre:** `Pop` · **Mood:** `Playful`, `Uplifting`, `Whimsical` · **Instruments:** `Piano`, `Strings`, `Bells`
**Additional details:**
> `"whimsical and playful, like a storybook come to life, twinkling music-box bells, bouncy piano, gentle strings, magical and joyful, light and airy, feels like turning the pages of a children's picture book"`

## Option 2 — Bright & bouncy (energetic, kid-friendly)
**Genre:** `Pop` · **Mood:** `Energetic`, `Happy`, `Playful` · **Instruments:** `Synth`, `Drums`, `Bells`
**Additional details:**
> `"bright and bouncy, cheerful cartoon energy, bouncy synth melody, light percussion, sparkly bells, upbeat and fun, like a happy animated short for kids, warm and inviting"`

## Option 3 — Magical adventure (storybook feel)
**Genre:** `Cinematic` · **Mood:** `Magical`, `Uplifting`, `Whimsical` · **Instruments:** `Strings`, `Piano`, `Bells`
**Additional details:**
> `"magical and adventurous, like a storybook journey, sweeping strings, gentle piano, sparkling chimes, wonder and delight, soft and dreamy with a joyful lift, perfect for an animated children's tale"`

## Option 4 — Short & punchy (if you want a snappy 15–20s promo)
**Genre:** `Pop` · **Mood:** `Playful`, `Energetic` · **Instruments:** `Synth`, `Bells`, `Drums`
**Additional details:**
> `"short, punchy, and fun, playful cartoon jingle, bouncy and bright, sparkly bells and light drums, cheerful and memorable, like a kids' app intro"`

**Recommendation:** **Option 1** — it matches the "storybook come to life" brand voice
("Change how people experience stories"). Set **Duration** to 30s, **Instrumental only**
ON, and turn on **Polish with AI**.

---

# Visual style prompts (pair with the audio)

## Pair with Option 1 (Playful & whimsical)
**Visual style / prompt:**
> `"whimsical storybook animation, soft watercolor and crayon textures, warm pastel palette with deep blue #003388 and orange #ffa902 accents, gentle hand-drawn characters, floating sparkles and twinkling stars, magical and joyful, like a children's picture book come to life"`

**Visual Treatment / Short Story:**
> `"A blank page opens like a book. A little fox character drawn in crayon steps out and begins to color the world around it — trees, a castle, a river. Each page turn brings a new scene to life with a gentle bounce. The fox waves as the Kwiksher logo appears, and the tagline 'Change how you experience stories' fades in."`

## Pair with Option 2 (Bright & bouncy)
**Visual style / prompt:**
> `"bright cartoon animation, bold clean shapes, cheerful saturated colors with blue #003388 and orange #ffa902, bouncy character motion, confetti and sparkles, energetic and fun, like a happy animated kids' app"`

**Visual Treatment / Short Story:**
> `"A cheerful robot taps a tablet and a storybook springs open. Pages flip fast with a bouncy rhythm, each one a colorful scene — a rocket launch, a dancing dinosaur, a rainbow. The robot gives a thumbs up as the logo pops in with a bounce."`

## Pair with Option 3 (Magical adventure)
**Visual style / prompt:**
> `"magical storybook animation, dreamy soft lighting, glowing lanterns and fireflies, rich deep blues with warm orange highlights, sweeping cinematic camera, wonder and delight, like an animated fairy tale"`

**Visual Treatment / Short Story:**
> `"A child blows on a dandelion and the seeds drift into a glowing storybook. The camera sweeps through a magical forest as pages turn — a dragon, a hidden castle, a starry sky. The seeds gather into the Kwiksher logo as the story gently closes."`

## Pair with Option 4 (Short & punchy)
**Visual style / prompt:**
> `"punchy cartoon motion graphics, bold flat shapes, playful squash-and-stretch, bright blue #003388 and orange #ffa902, quick snappy cuts, sparkles and stars, fun and memorable, like a kids' app intro"`

**Visual Treatment / Short Story:**
> `"Quick montage: a crayon draws a line, a page flips, a character pops up and waves, a rocket blasts off. Fast snappy cuts synced to the beat, ending on the Kwiksher logo with a star burst."`

**Settings to match (on `/music-video`):**
- **Director planning mode** → `Narrative continuity` (keeps the character consistent across cuts).
- **Use Director for distinct per-cut prompts** → ON.
- **I2V / Animation Model** → `Wan 2.2 5B TI2V` (MPS-friendly default).