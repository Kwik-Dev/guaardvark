"""
Google Indexing API submission + sitemap pulling for websites.

Three responsibilities:
  1. Sitemap parsing — fetch a site's sitemap (handles <sitemapindex> nesting and
     gzip), newest-first by <lastmod>, and enqueue new URLs as pending rows.
  2. Submission — authenticate with the service-account key and POST each URL to
     the Indexing API, recording the result on its row.
  3. Quota — a Redis-backed rolling-24h counter per site (mirrors the
     social_outreach kill_switch pattern) so we never exceed the daily cap.

DB-facing functions assume an active Flask app context (the API routes already
have one; the Celery tasks push one via `with app.app_context()`).

NOTE: The Indexing API is officially only for JobPosting / BroadcastEvent pages.
It commonly works to prompt crawling of ordinary pages but is not Google's
sanctioned use and may stop working without notice. Keep sitemaps current too.
"""

from __future__ import annotations

import gzip
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/indexing"]
ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"

_SM_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_TIMEOUT = 30
_HEADERS = {"User-Agent": "guaardvark-indexing/1.0"}

DEFAULT_DAILY_CAP = 190
REDIS_KEY_DAILY = "google_indexing:submitted_24h:{website_id}"  # zset of timestamps
_DAY_SECONDS = 24 * 3600


# --------------------------------------------------------------------------- #
# Credentials / HTTP session
# --------------------------------------------------------------------------- #

_session = None  # cached AuthorizedSession (handles its own token refresh)


def _key_path() -> Optional[str]:
    try:
        from backend import config

        return getattr(config, "GOOGLE_INDEXING_KEY_PATH", None)
    except Exception:
        return os.environ.get("GOOGLE_INDEXING_KEY_PATH")


def get_session():
    """Authorized requests session for the Indexing API, or None if unavailable."""
    global _session
    if _session is not None:
        return _session
    key_path = _key_path()
    if not key_path or not os.path.exists(key_path):
        logger.warning("Google Indexing key not found at %s", key_path)
        return None
    try:
        import google.auth.transport.requests
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(
            key_path, scopes=SCOPES
        )
        _session = google.auth.transport.requests.AuthorizedSession(creds)
        return _session
    except Exception as e:
        logger.error("Failed to build Google Indexing session: %s", e)
        return None


def credentials_ok() -> bool:
    return get_session() is not None


# --------------------------------------------------------------------------- #
# Redis quota (rolling 24h, per site)
# --------------------------------------------------------------------------- #


def _get_redis():
    try:
        import redis

        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)
        client.ping()
        return client
    except Exception as e:
        logger.warning("redis unavailable for indexing quota: %s", e)
        return None


def submitted_today(website_id: int) -> int:
    """Count of successful submissions in the last 24h for this site."""
    r = _get_redis()
    if r is None:
        return 0
    key = REDIS_KEY_DAILY.format(website_id=website_id)
    now = time.time()
    r.zremrangebyscore(key, 0, now - _DAY_SECONDS)
    return r.zcard(key)


def quota_remaining(website_id: int, daily_cap: int = DEFAULT_DAILY_CAP) -> int:
    return max(0, daily_cap - submitted_today(website_id))


def _record_submission(website_id: int) -> None:
    r = _get_redis()
    if r is None:
        logger.warning("record_submission: redis unavailable, quota will under-count")
        return
    key = REDIS_KEY_DAILY.format(website_id=website_id)
    now = time.time()
    r.zadd(key, {f"{now}": now})
    r.expire(key, _DAY_SECONDS + 3600)


# --------------------------------------------------------------------------- #
# Sitemap parsing
# --------------------------------------------------------------------------- #


def _parse_lastmod(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    text = text.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _fetch_bytes(url: str) -> bytes:
    resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.content
    if url.endswith(".gz") or data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError:
            pass
    return data


def _fetch_sitemap_urls(
    sitemap_url: str,
    _seen: Optional[set] = None,
    _depth: int = 0,
    max_depth: int = 5,
) -> list[tuple[str, Optional[datetime]]]:
    if _seen is None:
        _seen = set()
    if sitemap_url in _seen or _depth > max_depth:
        return []
    _seen.add(sitemap_url)

    data = _fetch_bytes(sitemap_url)
    root = ET.fromstring(data)
    tag = root.tag.split("}")[-1]

    results: list[tuple[str, Optional[datetime]]] = []
    if tag == "sitemapindex":
        for sm in root.findall(f"{_SM_NS}sitemap"):
            loc = sm.findtext(f"{_SM_NS}loc")
            if loc:
                results.extend(
                    _fetch_sitemap_urls(loc.strip(), _seen, _depth + 1, max_depth)
                )
    elif tag == "urlset":
        for u in root.findall(f"{_SM_NS}url"):
            loc = u.findtext(f"{_SM_NS}loc")
            if not loc:
                continue
            lastmod = _parse_lastmod(u.findtext(f"{_SM_NS}lastmod"))
            results.append((loc.strip(), lastmod))
    return results


def collect_sitemap_urls(
    sitemap_url: str, newest_first: bool = True
) -> list[tuple[str, Optional[datetime]]]:
    """Fetch all URLs, dedup, optionally sort newest-first by <lastmod>."""
    pairs = _fetch_sitemap_urls(sitemap_url)
    seen: set = set()
    deduped: list[tuple[str, Optional[datetime]]] = []
    for url, lm in pairs:
        if url not in seen:
            seen.add(url)
            deduped.append((url, lm))
    if newest_first:
        deduped.sort(
            key=lambda p: (
                p[1] is not None,
                p[1] or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
    return deduped


def _default_sitemap(site_url: str) -> Optional[str]:
    try:
        parsed = urlparse(site_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- #
# DB-facing operations (require active app context)
# --------------------------------------------------------------------------- #


def _get_or_create_config(website_id: int):
    from backend.models import GoogleIndexingConfig, db

    cfg = (
        db.session.query(GoogleIndexingConfig)
        .filter_by(website_id=website_id)
        .first()
    )
    if cfg is None:
        cfg = GoogleIndexingConfig(website_id=website_id, daily_cap=DEFAULT_DAILY_CAP)
        db.session.add(cfg)
        db.session.flush()
    return cfg


def sync_sitemap(website_id: int) -> dict:
    """Pull the site's sitemap and enqueue new URLs as pending submissions."""
    from backend.models import GoogleIndexingSubmission, Website, db

    site = db.session.get(Website, website_id)
    if not site:
        return {"success": False, "error": "Website not found"}

    sitemap_url = (site.sitemap or "").strip() or _default_sitemap(site.url)
    if not sitemap_url:
        return {"success": False, "error": "No sitemap URL for this website"}

    try:
        pairs = collect_sitemap_urls(sitemap_url, newest_first=True)
    except Exception as e:
        logger.error("sitemap fetch failed for %s: %s", sitemap_url, e)
        return {"success": False, "error": f"Sitemap fetch failed: {e}"}

    existing = {
        row[0]
        for row in db.session.query(GoogleIndexingSubmission.url)
        .filter_by(website_id=website_id)
        .all()
    }
    notif = _get_or_create_config(website_id).notification_type or "URL_UPDATED"
    added = 0
    for url, _lm in pairs:
        if url in existing:
            continue
        db.session.add(
            GoogleIndexingSubmission(
                website_id=website_id,
                url=url,
                notification_type=notif,
                status="pending",
            )
        )
        existing.add(url)
        added += 1

    cfg = _get_or_create_config(website_id)
    cfg.last_sitemap_sync = datetime.now()
    db.session.commit()
    return {
        "success": True,
        "sitemap": sitemap_url,
        "total_in_sitemap": len(pairs),
        "newly_queued": added,
    }


def submit_url(session, url: str, notification_type: str = "URL_UPDATED"):
    """POST one URL. Returns (http_status, detail_text)."""
    body = {"url": url, "type": notification_type}
    resp = session.post(ENDPOINT, json=body, timeout=_TIMEOUT)
    return resp.status_code, (resp.text or "").strip().replace("\n", " ")[:500]


def process_site_batch(website_id: int, max_n: Optional[int] = None) -> dict:
    """Submit pending URLs for a site, bounded by the daily quota and max_n."""
    from backend.models import GoogleIndexingSubmission, db

    cfg = _get_or_create_config(website_id)
    cap = cfg.daily_cap or DEFAULT_DAILY_CAP
    notif = cfg.notification_type or "URL_UPDATED"
    db.session.commit()  # persist config if it was just created

    remaining = quota_remaining(website_id, cap)
    if remaining <= 0:
        return {
            "success": True,
            "submitted": 0,
            "failed": 0,
            "reason": "daily quota reached",
            "quota_remaining": 0,
        }

    session = get_session()
    if session is None:
        return {"success": False, "error": "Google Indexing credentials unavailable"}

    limit = remaining if max_n is None else min(remaining, max_n)
    pending = (
        db.session.query(GoogleIndexingSubmission)
        .filter_by(website_id=website_id, status="pending")
        .order_by(GoogleIndexingSubmission.id.asc())  # id asc == newest-first insert order
        .limit(limit)
        .all()
    )

    ok = fail = 0
    quota_hit = False
    for sub in pending:
        if quota_remaining(website_id, cap) <= 0:
            break
        try:
            code, detail = submit_url(session, sub.url, notif)
        except Exception as e:
            sub.status = "failed"
            sub.error = str(e)[:500]
            sub.submitted_at = datetime.now()
            db.session.commit()
            fail += 1
            continue

        sub.http_status = code
        if code == 200:
            sub.status = "success"
            sub.error = None
            sub.submitted_at = datetime.now()
            _record_submission(website_id)
            ok += 1
        elif code == 429:
            # Server-side quota exhausted — leave this one pending and stop.
            quota_hit = True
            db.session.commit()
            break
        else:
            sub.status = "failed"
            sub.error = detail
            sub.submitted_at = datetime.now()
            fail += 1
        db.session.commit()

    cfg = _get_or_create_config(website_id)
    cfg.last_run_at = datetime.now()
    db.session.commit()

    result = {
        "success": True,
        "submitted": ok,
        "failed": fail,
        "quota_remaining": quota_remaining(website_id, cap),
    }
    if quota_hit:
        result["reason"] = "google returned 429 (daily quota)"
    return result


def get_status(website_id: int) -> dict:
    """Snapshot for the UI: config, quota, and queue counts."""
    from backend.models import GoogleIndexingSubmission, db

    cfg = _get_or_create_config(website_id)
    cap = cfg.daily_cap or DEFAULT_DAILY_CAP
    cfg_dict = cfg.to_dict()
    db.session.commit()  # persist config if newly created

    counts = {"pending": 0, "success": 0, "failed": 0}
    rows = (
        db.session.query(GoogleIndexingSubmission.status, db.func.count())
        .filter_by(website_id=website_id)
        .group_by(GoogleIndexingSubmission.status)
        .all()
    )
    for status, n in rows:
        counts[status] = n

    return {
        "website_id": website_id,
        "enabled": cfg_dict["enabled"],
        "daily_cap": cap,
        "notification_type": cfg_dict["notification_type"],
        "submitted_today": submitted_today(website_id),
        "quota_remaining": quota_remaining(website_id, cap),
        "queue": counts,
        "last_sitemap_sync": cfg_dict["last_sitemap_sync"],
        "last_run_at": cfg_dict["last_run_at"],
        "credentials_ok": credentials_ok(),
    }


def update_config(website_id: int, **fields) -> dict:
    """Update per-site config (enabled / daily_cap / notification_type)."""
    from backend.models import db

    cfg = _get_or_create_config(website_id)
    if "enabled" in fields and fields["enabled"] is not None:
        cfg.enabled = bool(fields["enabled"])
    if "daily_cap" in fields and fields["daily_cap"] is not None:
        try:
            cfg.daily_cap = max(1, min(200, int(fields["daily_cap"])))
        except (ValueError, TypeError):
            pass
    if "notification_type" in fields and fields["notification_type"] in (
        "URL_UPDATED",
        "URL_DELETED",
    ):
        cfg.notification_type = fields["notification_type"]
    db.session.commit()
    return cfg.to_dict()
