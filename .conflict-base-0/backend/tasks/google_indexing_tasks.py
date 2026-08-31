"""Google Indexing API Celery tasks.

Two tasks:
  - submit_indexing_batch_for_site: on-demand (the "Submit to Index" button).
    Syncs the sitemap, then submits up to the remaining daily quota for one site.
  - indexing_drip_tick: Beat-driven (every 15 min). For every site with auto-drip
    enabled, submits a small batch — bounded by the per-site daily quota, so the
    real ceiling is daily_cap/site regardless of how often the tick fires. Also
    re-syncs a site's sitemap once its last sync is older than ~24h, so enabled
    sites stay hands-off.

DB access requires the Flask app context, obtained the same way as the other
db-touching tasks: `from backend.app import app` + `with app.app_context()`.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from celery import shared_task

logger = logging.getLogger(__name__)

# URLs submitted per enabled site per Beat tick. With a 15-min tick that's
# 96 ticks/day; the per-site daily quota still caps the real total.
PER_TICK_PER_SITE = int(os.environ.get("GOOGLE_INDEXING_PER_TICK", "3"))
RESYNC_AFTER_HOURS = int(os.environ.get("GOOGLE_INDEXING_RESYNC_HOURS", "24"))


@shared_task(
    name="google_indexing.submit_batch_for_site",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def submit_indexing_batch_for_site(self, website_id, max_n=None, sync_first=True):
    """On-demand: sync the sitemap then submit up to the daily quota for one site."""
    from backend.app import app

    with app.app_context():
        from backend.services import google_indexing_service as gis

        result = {"website_id": website_id}
        if sync_first:
            result["sync"] = gis.sync_sitemap(website_id)
        result["batch"] = gis.process_site_batch(website_id, max_n=max_n)
        logger.info("Indexing batch for site %s: %s", website_id, result.get("batch"))
        return result


@shared_task(name="google_indexing.drip_tick", bind=True)
def indexing_drip_tick(self):
    """Beat-driven: drip a small batch for every auto-drip-enabled site."""
    from backend.app import app

    with app.app_context():
        from backend.models import GoogleIndexingConfig, db
        from backend.services import google_indexing_service as gis

        enabled = (
            db.session.query(GoogleIndexingConfig).filter_by(enabled=True).all()
        )
        results = []
        resync_cutoff = datetime.now() - timedelta(hours=RESYNC_AFTER_HOURS)
        for cfg in enabled:
            wid = cfg.website_id
            try:
                # Keep the queue fed: re-sync if we've never synced or it's stale.
                if cfg.last_sitemap_sync is None or cfg.last_sitemap_sync < resync_cutoff:
                    gis.sync_sitemap(wid)
                res = gis.process_site_batch(wid, max_n=PER_TICK_PER_SITE)
                results.append(
                    {
                        "website_id": wid,
                        "submitted": res.get("submitted"),
                        "failed": res.get("failed"),
                        "quota_remaining": res.get("quota_remaining"),
                    }
                )
            except Exception as e:
                logger.error("drip tick failed for site %s: %s", wid, e)
                results.append({"website_id": wid, "error": str(e)})
        return {"enabled_sites": len(enabled), "results": results}
