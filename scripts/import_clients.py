#!/usr/bin/env python3
"""
import_clients.py — bulk-register Guaardvark clients from a CSV or Markdown file.

The backend endpoint is POST /api/clients/ (see backend/api/clients_api.py).
Only `name` is required; every other field is optional.

Usage:
    python3 scripts/import_clients.py clients.csv
    python3 scripts/import_clients.py clients.md
    python3 scripts/import_clients.py clients.csv --server http://localhost:5000
    python3 scripts/import_clients.py clients.csv --dry-run

Environment:
    GUAARDVARK_API_KEY  (or LLX_API_KEY) — sent as X-API-Key if the backend requires it.

CSV format (header row = field names, one client per row):
    name,email,phone,location,notes,industry,target_audience,unique_selling_points,
    competitor_urls,brand_voice_examples,keywords,content_goals,regulatory_constraints,
    geographic_coverage

    Array fields (industry, target_audience, unique_selling_points, competitor_urls,
    keywords, content_goals, geographic_coverage) accept a `|`-separated list, e.g.:
        industry = "Healthcare|Legal"

Markdown format (one client per file, or one per `---`-separated block):

    ---
    name: Acme Corp
    email: hello@acme.com
    phone: +1-555-0100
    location: Miami, FL
    notes: Long-term retainer client.
    industry: Healthcare, Legal
    target_audience: Small Business Owners, Healthcare Professionals
    unique_selling_points: Specialized Expertise, Award-Winning Service
    competitor_urls: https://rival1.com, https://rival2.com
    brand_voice_examples: |
        We speak plainly and put patients first.
    keywords: telehealth, HIPAA, patient portal
    content_goals: Lead Generation, Thought Leadership
    regulatory_constraints: HIPAA, GDPR
    geographic_coverage: Miami, Tampa, Orlando
    ---

    (Array fields are comma-separated. `|`-style YAML block scalars are supported
    for brand_voice_examples / regulatory_constraints / notes.)

    A `website` field is also accepted (e.g. `website: https://acme.com`). It is NOT
    sent to the API; it is used only by the `--followup` option to download the site's
    logo / favicon / hero image and save analysis data into a per-client folder.

Followup folder (--followup):
    python3 scripts/import_clients.py clients.md --followup
    python3 scripts/import_clients.py clients.md --followup --assets-dir data/clients/followup

    For each client with a `website`, creates:
        <assets-dir>/<slug>/
        ├── client.md            # the import file
        ├── assets/             # logo, favicon, og-image, hero (downloaded)
        └── data/analysis.json  # extracted site data + asset URLs

    Add --video to also gather a promotion-video kit into <slug>/video/:
        images/   # all page images (product shots, screenshots, portfolio)
        videos/   # any <video>/<source> or .mp4/.webm/.mov found
        video_kit.json  # brand colors, meta, testimonials, socials, asset URLs
"""

import argparse
import csv
import json
import os
import re
import sys
import urllib.request
import urllib.error
from urllib.parse import urljoin, urlparse

# Fields that the backend stores as JSON arrays (see clients_api.py).
ARRAY_FIELDS = {
    "industry",
    "target_audience",
    "unique_selling_points",
    "competitor_urls",
    "keywords",
    "content_goals",
    "geographic_coverage",
}

# Fields the backend accepts on create (superset of what the UI shows).
ALLOWED_FIELDS = {
    "name",
    "email",
    "phone",
    "location",
    "logo_path",
    "notes",
    *ARRAY_FIELDS,
    "brand_voice_examples",
    "regulatory_constraints",
    # Used only by --followup (asset download); never sent to the API.
    "website",
}

# Fields that are NOT part of the API payload (kept for followup only).
NON_API_FIELDS = {"website"}

# Default base directory for followup folders.
DEFAULT_ASSETS_DIR = os.path.join("data", "clients", "followup")


def split_list(value):
    """Turn a string into a list, splitting on commas or pipes."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    sep = "|" if "|" in text else ","
    return [part.strip() for part in text.split(sep) if part.strip()]


def normalize(record):
    """Build the JSON body the API expects, dropping empty/unknown fields."""
    body = {}
    for key, value in record.items():
        key = key.strip().lower().replace(" ", "_")
        if key not in ALLOWED_FIELDS:
            continue
        if key in ARRAY_FIELDS:
            items = split_list(value)
            if items:
                body[key] = items
        else:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                body[key] = text
    return body


def parse_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        return [normalize(row) for row in reader]


def parse_md(path):
    """Parse one or more YAML-front-matter blocks separated by `---`."""
    import re

    text = open(path, encoding="utf-8").read()
    # Split on lines that are exactly '---'
    blocks = re.split(r"^\s*---\s*$", text, flags=re.MULTILINE)
    records = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        # Parse simple key: value lines; handle '|' block scalars.
        record = {}
        lines = block.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if not line.strip() or line.lstrip().startswith("#"):
                i += 1
                continue
            if ":" not in line:
                i += 1
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            # Block scalar: value is '|' and following indented lines belong to it.
            if value == "|":
                collected = []
                i += 1
                while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                    collected.append(lines[i].strip())
                    i += 1
                record[key] = "\n".join(collected)
                continue
            record[key] = value
            i += 1
        if record:
            records.append(normalize(record))
    return records


def post_client(server, api_key, body, dry_run):
    if dry_run:
        return {"dry_run": True, "body": body}
    url = server.rstrip("/") + "/api/clients/"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        return {"error": f"HTTP {e.code}: {detail}"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Followup folder: download logo / favicon / hero + save analysis data.
# ---------------------------------------------------------------------------


def slugify(name):
    """Lowercase, non-alphanumeric -> '-'. e.g. 'Kwiksher' -> 'kwiksher'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "client"


def fetch_html(url):
    """Fetch a page's HTML as text, or None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None


def extract_assets(html, base_url):
    """Find logo, favicon, og-image, and a hero image URL from page HTML."""
    assets = {}

    def first(pattern):
        m = re.search(pattern, html, re.I)
        return urljoin(base_url, m.group(1)) if m else None

    # og:image (property before content, or content before property)
    og = first(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)')
    if not og:
        og = first(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']')
    if og:
        assets["og-image"] = og

    # apple-touch-icon is the best logo source (PNG, high-res)
    logo = first(r'<link[^>]+rel=["\']apple-touch-icon["\'][^>]+href=["\']([^"\']+)')
    if not logo:
        logo = first(r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']apple-touch-icon["\']')
    # fall back to a header <img> with 'logo' in its class (either attribute order)
    if not logo:
        logo = first(r'<img[^>]+class=["\'][^"\']*logo[^"\']*["\'][^>]+src=["\']([^"\']+)')
    if not logo:
        logo = first(r'<img[^>]+src=["\']([^"\']+)["\'][^>]+class=["\'][^"\']*logo[^"\']*["\']')
    if logo:
        assets["logo"] = logo

    # favicon
    fav = first(r'<link[^>]+rel=["\'](?:shortcut )?icon["\'][^>]+href=["\']([^"\']+)')
    if not fav:
        fav = first(r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\'](?:shortcut )?icon["\']')
    if fav:
        assets["favicon"] = fav

    # hero: prefer og-image; else first non-logo/non-icon image that is NOT a
    # small thumbnail (URLs like '-300x225.jpg' are WordPress resized crops).
    # Check both `src` and `data-lazyload` (lazy-loaded slider images).
    if "hero" not in assets:
        candidates = re.findall(
            r'<img[^>]+(?:src|data-lazyload)=["\']([^"\']+)["\']', html, re.I
        )
        for src in candidates:
            low = src.lower()
            if "logo" in low or "icon" in low or "dummy" in low or "spacer" in low:
                continue
            if re.search(r"-\d+x\d+(?:\.\w+)?$", low):
                continue  # skip resized thumbnail
            assets["hero"] = urljoin(base_url, src)
            break
    return assets


def download(url, dest):
    """Download a URL to a local file. Returns dest on success, raises on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    with open(dest, "wb") as fh:
        fh.write(data)
    return dest


def write_client_md(record, path):
    """Write a single client record back out as an importable .md block."""
    lines = ["---"]
    for key, value in record.items():
        if key in NON_API_FIELDS:
            continue
        if isinstance(value, list):
            lines.append(f"{key}: {', '.join(value)}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif"}


def append_followup_references(md_path, folder):
    """Append a references section to client.md listing the ACTUAL files gathered
    in the followup folder (assets/, data/, video/). Image files are embedded as
    markdown images (viewable in a markdown preview); other files are listed as
    code paths. Paths are relative to `folder` so the embeds resolve."""
    lines = []
    for sub in ["assets", "data", "video"]:
        sub_dir = os.path.join(folder, sub)
        if not os.path.isdir(sub_dir):
            continue
        files = []
        for root, _dirs, filenames in os.walk(sub_dir):
            for fn in sorted(filenames):
                files.append(os.path.relpath(os.path.join(root, fn), folder))
        if not files:
            continue
        lines.append(f"- **{sub.title()}:**")
        for f in files:
            if os.path.splitext(f)[1].lower() in IMAGE_EXTS:
                lines.append(f"  - ![`{os.path.basename(f)}`]({f})")
            else:
                lines.append(f"  - `{f}`")
    if not lines:
        return
    with open(md_path, "a", encoding="utf-8") as fh:
        fh.write("\n## Followup assets\n\n")
        fh.write("\n".join(lines) + "\n")


def create_followup(record, base_dir, logo_url=None, hero_url=None):
    """Create the followup folder for one client: client.md + assets + analysis.json.

    logo_url / hero_url explicitly override the auto-detected logo / hero.
    Returns the folder path (or None if no website was provided).
    """
    name = record.get("name", "client")
    website = record.get("website")
    if not website:
        return None

    folder = os.path.join(base_dir, slugify(name))
    assets_dir = os.path.join(folder, "assets")
    data_dir = os.path.join(folder, "data")
    os.makedirs(assets_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)

    write_client_md(record, os.path.join(folder, "client.md"))

    analysis = {
        "name": name,
        "website": website,
        "record": {k: v for k, v in record.items() if k not in NON_API_FIELDS},
    }

    html = fetch_html(website)
    assets = {}
    if html:
        assets = extract_assets(html, website)
    else:
        analysis["asset_errors"] = {"page": "could not fetch website HTML"}

    # Explicit overrides take precedence over auto-detected assets.
    if logo_url:
        assets["logo"] = logo_url
    if hero_url:
        assets["hero"] = hero_url

    for role, url in assets.items():
        ext = os.path.splitext(urlparse(url).path)[1] or ".png"
        dest = os.path.join(assets_dir, f"{role}{ext}")
        try:
            download(url, dest)
            analysis.setdefault("assets", {})[role] = url
        except Exception as e:  # noqa: BLE001
            analysis.setdefault("asset_errors", {})[role] = str(e)

    with open(os.path.join(data_dir, "analysis.json"), "w", encoding="utf-8") as fh:
        json.dump(analysis, fh, indent=2, ensure_ascii=False)

    return folder


# ---------------------------------------------------------------------------
# Promotion-video kit: gather images, videos, brand colors, messaging, socials.
# ---------------------------------------------------------------------------


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)


def extract_meta(html):
    """Extract <title>, meta description, and Open Graph tags."""
    meta = {}
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        meta["title"] = m.group(1).strip()
    for prop in ["og:title", "og:description", "og:image", "og:type", "og:site_name"]:
        m = re.search(
            r'<meta[^>]+property=["\']' + re.escape(prop) + r'["\'][^>]+content=["\']([^"\']+)',
            html, re.I,
        )
        if not m:
            m = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']' + re.escape(prop) + r'["\']',
                html, re.I,
            )
        if m:
            meta[prop] = m.group(1).strip()
    return meta


def extract_all_images(html, base_url):
    """Collect all image URLs (src + data-lazyload), dedup, skip tiny/icon/dummy."""
    urls, seen = [], set()
    for src in re.findall(r'<img[^>]+(?:src|data-lazyload)=["\']([^"\']+)["\']', html, re.I):
        url = urljoin(base_url, src)
        if url in seen:
            continue
        seen.add(url)
        low = url.lower()
        if "dummy" in low or "spacer" in low or "icon" in low:
            continue
        urls.append(url)
    return urls


def extract_videos(html, base_url):
    """Find video sources: <video>/<source> src and direct .mp4/.webm/.mov links."""
    videos, seen = [], set()
    patterns = [
        r'<video[^>]+src=["\']([^"\']+)["\']',
        r'<source[^>]+src=["\']([^"\']+)["\']',
        r'["\']([^"\']+\.(?:mp4|webm|mov))["\']',
    ]
    for pat in patterns:
        for src in re.findall(pat, html, re.I):
            url = urljoin(base_url, src)
            if url not in seen:
                seen.add(url)
                videos.append(url)
    return videos


def extract_brand_colors(html):
    """Extract hex colors from inline CSS; return a palette + dominant color."""
    from collections import Counter
    norm = []
    for c in re.findall(r"#[0-9a-fA-F]{3,8}\b", html):
        c = c.lower()
        if len(c) == 4:  # #abc -> #aabbcc
            c = "#" + "".join(ch * 2 for ch in c[1:])
        if len(c) == 7:
            norm.append(c)
    counts = Counter(norm)
    palette = [c for c, _ in counts.most_common(8)]
    return {"palette": palette, "dominant": palette[0] if palette else None}


def extract_social_links(html, base_url):
    """Find social profile links (facebook, x/twitter, instagram, linkedin, youtube, tiktok)."""
    socials = {}
    for src in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
        url = urljoin(base_url, src)
        low = url.lower()
        for name in ["facebook", "twitter", "x.com", "instagram", "linkedin", "youtube", "tiktok", "pinterest"]:
            if name in low and name not in socials:
                socials[name] = url
    return socials


def extract_testimonials(html):
    """Find blockquote text (testimonials / quotes) for video voiceover."""
    texts = []
    for q in re.findall(r"<blockquote[^>]*>(.*?)</blockquote>", html, re.I | re.S):
        text = re.sub(r"<[^>]+>", " ", q)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            texts.append(text)
    return texts[:10]


def create_video_kit(record, folder, video_urls=None):
    """Gather a promotion-video kit into <folder>/video/: images, videos, brand,
    messaging, socials, and a video_kit.json manifest.

    video_urls: optional list of explicit video URLs to download in addition to
    any auto-detected from the page.
    Returns the video folder path, or None if no website was provided.
    """
    website = record.get("website")
    if not website:
        return None

    video_dir = os.path.join(folder, "video")
    images_dir = os.path.join(video_dir, "images")
    videos_dir = os.path.join(video_dir, "videos")
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(videos_dir, exist_ok=True)

    html = fetch_html(website)
    kit = {
        "name": record.get("name"),
        "website": website,
        "meta": {},
        "brand": {},
        "messaging": {},
        "socials": {},
        "assets": {"images": [], "videos": []},
        "errors": {},
    }
    if not html:
        kit["errors"]["page"] = "could not fetch website HTML"
        _write_json(os.path.join(video_dir, "video_kit.json"), kit)
        return video_dir

    kit["meta"] = extract_meta(html)
    kit["brand"]["colors"] = extract_brand_colors(html)
    kit["socials"] = extract_social_links(html, website)
    kit["messaging"]["testimonials"] = extract_testimonials(html)

    for url in extract_all_images(html, website):
        ext = os.path.splitext(urlparse(url).path)[1] or ".png"
        dest = os.path.join(images_dir, f"img_{len(kit['assets']['images'])}{ext}")
        try:
            download(url, dest)
            kit["assets"]["images"].append(url)
        except Exception as e:  # noqa: BLE001
            kit["errors"].setdefault("images", []).append(f"{url}: {e}")

    for url in extract_videos(html, website):
        ext = os.path.splitext(urlparse(url).path)[1] or ".mp4"
        dest = os.path.join(videos_dir, f"video_{len(kit['assets']['videos'])}{ext}")
        try:
            download(url, dest)
            kit["assets"]["videos"].append(url)
        except Exception as e:  # noqa: BLE001
            kit["errors"].setdefault("videos", []).append(f"{url}: {e}")

    # Explicit video URLs (--video-url) in addition to auto-detected ones.
    for url in (video_urls or []):
        ext = os.path.splitext(urlparse(url).path)[1] or ".mp4"
        dest = os.path.join(videos_dir, f"video_{len(kit['assets']['videos'])}{ext}")
        try:
            download(url, dest)
            kit["assets"]["videos"].append(url)
        except Exception as e:  # noqa: BLE001
            kit["errors"].setdefault("videos", []).append(f"{url}: {e}")

    _write_json(os.path.join(video_dir, "video_kit.json"), kit)
    return video_dir


def main():
    ap = argparse.ArgumentParser(description="Bulk-register Guaardvark clients.")
    ap.add_argument("file", help="Path to .csv or .md file")
    ap.add_argument("--server", default=os.environ.get("GUAARDVARK_SERVER", "http://localhost:5000"))
    ap.add_argument("--dry-run", action="store_true", help="Validate/print only, don't POST")
    ap.add_argument("--followup", action="store_true",
                    help="Create a followup folder (client.md + logo/favicon/hero assets + analysis.json) for each client with a 'website' field")
    ap.add_argument("--video", action="store_true",
                    help="Also gather a promotion-video kit (images, videos, brand colors, testimonials, socials, video_kit.json) into the followup folder")
    ap.add_argument("--logo", default=None, metavar="URL",
                    help="Explicit logo URL to download (overrides auto-detected logo)")
    ap.add_argument("--hero", default=None, metavar="URL",
                    help="Explicit hero image URL to download (overrides auto-detected hero)")
    ap.add_argument("--video-url", action="append", default=[], metavar="URL",
                    help="Explicit video URL to download into the video kit (repeatable, or comma-separated)")
    ap.add_argument("--assets-dir", default=DEFAULT_ASSETS_DIR,
                    help=f"Base directory for followup folders (default: {DEFAULT_ASSETS_DIR})")
    args = ap.parse_args()

    api_key = os.environ.get("GUAARDVARK_API_KEY") or os.environ.get("LLX_API_KEY")

    ext = os.path.splitext(args.file)[1].lower()
    if ext == ".csv":
        records = parse_csv(args.file)
    elif ext in (".md", ".markdown"):
        records = parse_md(args.file)
    else:
        sys.exit(f"Unsupported file type '{ext}'. Use .csv or .md")

    if not records:
        sys.exit("No client records found in file.")

    print(f"Found {len(records)} client record(s).")
    created, failed = 0, 0
    for i, body in enumerate(records, 1):
        if not body.get("name"):
            print(f"[{i}/{len(records)}] SKIP (no name): {body}")
            failed += 1
            continue

        # 'website' is followup-only; strip it from the API payload.
        website = body.pop("website", None)

        result = post_client(args.server, api_key, body, args.dry_run)
        if result.get("dry_run"):
            print(f"[{i}/{len(records)}] DRY-RUN: {body.get('name')} -> {json.dumps(body)}")
        elif "error" in result:
            print(f"[{i}/{len(records)}] FAIL {body.get('name')}: {result['error']}")
            failed += 1
        else:
            print(f"[{i}/{len(records)}] OK   {body.get('name')} (id {result.get('id')})")
            created += 1

        if args.followup or args.video:
            if website:
                folder = create_followup({**body, "website": website}, args.assets_dir,
                                         logo_url=args.logo, hero_url=args.hero)
                print(f"      followup: {folder}")
                if args.video:
                    video_urls = [u for v in args.video_url for u in v.split(",") if u.strip()]
                    vdir = create_video_kit({**body, "website": website}, folder,
                                             video_urls=video_urls)
                    print(f"      video kit: {vdir}")
                append_followup_references(os.path.join(folder, "client.md"), folder)
            else:
                print(f"      followup: skipped (no 'website' field for {body.get('name')})")

    print(f"\nDone. Created: {created}, Failed: {failed}.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
