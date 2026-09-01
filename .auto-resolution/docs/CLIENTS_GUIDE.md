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

