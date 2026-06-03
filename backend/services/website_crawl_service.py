"""Crawl a website's sitemap and persist each page as a WebsitePage row.

This is the real crawl behind the (formerly placebo) Crawl button. It:
  1. resolves the site's sitemap (Website.sitemap → robots/common paths → /sitemap.xml),
  2. expands it into a deduped, newest-first list of page URLs (reusing the Google
     Indexing service's sitemap parser — no second XML parser),
  3. fetches up to `max_pages` of the URLs not already stored (idempotent via the
     unique (website_id, url) constraint), with a polite inter-fetch delay,
  4. upserts a WebsitePage per URL, capturing per-URL errors instead of aborting.

It reports added / skipped / failed / capped counts so nothing is silently truncated.
Designed to run inside a Celery Task (see backend/services/website_jobs/job_service.py).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES = 200
DEFAULT_DELAY_SECONDS = 0.5


def _resolve_sitemap_url(site) -> Optional[str]:
    """Best-effort sitemap discovery for a Website row."""
    if getattr(site, "sitemap", None):
        sm = site.sitemap.strip()
        if sm:
            return sm
    # Fall back to robots.txt + common sitemap paths, then /sitemap.xml.
    try:
        from backend.utils.web_scraper import _discover_sitemaps

        discovered = _discover_sitemaps(site.url, requests.Session())
        if discovered:
            return discovered[0]
    except Exception as exc:  # pragma: no cover - discovery is best-effort
        logger.warning("Sitemap discovery failed for %s: %s", site.url, exc)
    try:
        from backend.services.google_indexing_service import _default_sitemap

        return _default_sitemap(site.url)
    except Exception:
        return None


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    """Drop tzinfo so the value fits a naive TIMESTAMP column cleanly."""
    if dt is not None and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def crawl_website_sitemap(
    website_id: int,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay: float = DEFAULT_DELAY_SECONDS,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> dict:
    """Crawl a website's sitemap, persisting each new page as a WebsitePage.

    Returns a summary dict: {success, sitemap_url, total_in_sitemap, added,
    skipped, failed, capped}. Caller is expected to hold a Flask app context.
    """
    from backend.models import Website, WebsitePage, db
    from backend.services.google_indexing_service import collect_sitemap_urls
    from backend.utils.web_scraper import scrape_website

    def _progress(pct: int, msg: str) -> None:
        if progress_callback:
            try:
                progress_callback(pct, msg)
            except Exception:
                pass

    site = db.session.get(Website, website_id)
    if site is None:
        return {"success": False, "error": "Website not found", "website_id": website_id}

    try:
        max_pages = max(1, int(max_pages))
    except (TypeError, ValueError):
        max_pages = DEFAULT_MAX_PAGES

    _progress(15, "Resolving sitemap…")
    sitemap_url = _resolve_sitemap_url(site)
    if not sitemap_url:
        return {
            "success": False,
            "error": "No sitemap found (set the site's sitemap URL in settings).",
            "website_id": website_id,
        }

    _progress(25, f"Reading sitemap: {sitemap_url}")
    try:
        pairs = collect_sitemap_urls(sitemap_url, newest_first=True)
    except Exception as exc:
        logger.error("Sitemap fetch failed for %s: %s", sitemap_url, exc, exc_info=True)
        return {
            "success": False,
            "error": f"Sitemap fetch failed: {exc}",
            "sitemap_url": sitemap_url,
            "website_id": website_id,
        }

    total = len(pairs)
    # URLs we already have — re-crawl only fetches genuinely new pages.
    existing_urls = {
        row.url
        for row in db.session.query(WebsitePage.url)
        .filter(WebsitePage.website_id == website_id)
        .all()
    }

    added = skipped = failed = capped = 0
    fetched = 0
    for url, lastmod in pairs:
        if url in existing_urls:
            skipped += 1
            continue
        if fetched >= max_pages:
            # Bounded crawl — count (don't fetch) new URLs beyond the cap so the
            # summary reports exactly how many were left for the next run.
            capped += 1
            continue
        fetched += 1
        pct = 25 + int(60 * fetched / max(1, min(max_pages, total)))
        _progress(min(pct, 88), f"Crawling page {fetched}/{min(max_pages, total)}…")
        try:
            data = scrape_website(url)
            meta = data.get("metadata") or {}
            page = WebsitePage(
                website_id=website_id,
                url=url,
                title=data.get("title") or None,
                content=data.get("content") or None,
                slug=(data.get("slug") or None),
                meta_description=meta.get("description"),
                meta_keywords=data.get("keywords") or meta.get("keywords"),
                featured_image=data.get("featured_image") or None,
                og_metadata=_dump_meta(meta),
                last_modified_sitemap=_naive(lastmod),
                status="crawled",
                crawled_at=datetime.now(),
            )
            db.session.add(page)
            db.session.commit()
            added += 1
            existing_urls.add(url)
        except Exception as exc:
            db.session.rollback()
            failed += 1
            logger.warning("Failed to crawl %s: %s", url, exc)
            try:
                db.session.add(
                    WebsitePage(
                        website_id=website_id,
                        url=url,
                        status="error",
                        error_message=str(exc)[:2000],
                        crawled_at=datetime.now(),
                    )
                )
                db.session.commit()
                existing_urls.add(url)
            except Exception:
                db.session.rollback()
        if delay:
            time.sleep(delay)

    _progress(95, "Finalizing crawl…")
    return {
        "success": True,
        "sitemap_url": sitemap_url,
        "total_in_sitemap": total,
        "added": added,
        "skipped": skipped,
        "failed": failed,
        "capped": capped,
    }


def _dump_meta(meta: dict) -> Optional[str]:
    if not meta:
        return None
    try:
        import json

        return json.dumps(meta)[:20000]
    except (TypeError, ValueError):
        return None
