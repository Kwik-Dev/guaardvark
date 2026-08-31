# backend/api/google_indexing_api.py
# Google Search Console / Indexing API URL submission, scoped per website.
# Blueprint auto-registers via backend.utils.blueprint_discovery.

import logging

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from backend.models import GoogleIndexingSubmission, Website, db

search_console_bp = Blueprint(
    "search_console_api", __name__, url_prefix="/api/search-console"
)
logger = logging.getLogger(__name__)


def _require_website(website_id):
    """Return (website, error_response). One of them is None."""
    site = db.session.get(Website, website_id)
    if site is None:
        return None, (jsonify({"error": "Website not found"}), 404)
    return site, None


@search_console_bp.route("/<int:website_id>/status", methods=["GET"])
def status_route(website_id):
    site, err = _require_website(website_id)
    if err:
        return err
    try:
        from backend.services import google_indexing_service as gis

        return jsonify(gis.get_status(website_id)), 200
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error("status DB error for site %s: %s", website_id, e, exc_info=True)
        return jsonify({"error": "Database error", "details": str(e)}), 500
    except Exception as e:
        logger.error("status error for site %s: %s", website_id, e, exc_info=True)
        return jsonify({"error": "Unexpected error", "details": str(e)}), 500


@search_console_bp.route("/<int:website_id>/sync", methods=["POST"])
def sync_route(website_id):
    """Pull the site's sitemap and enqueue new URLs (no submission)."""
    site, err = _require_website(website_id)
    if err:
        return err
    try:
        from backend.services import google_indexing_service as gis

        result = gis.sync_sitemap(website_id)
        return jsonify(result), (200 if result.get("success") else 400)
    except Exception as e:
        db.session.rollback()
        logger.error("sync error for site %s: %s", website_id, e, exc_info=True)
        return jsonify({"error": "Unexpected error", "details": str(e)}), 500


@search_console_bp.route("/<int:website_id>/submit", methods=["POST"])
def submit_route(website_id):
    """Queue a background job that syncs the sitemap then submits up to quota."""
    site, err = _require_website(website_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    sync_first = data.get("sync", True)
    max_n = data.get("max_n")  # None => up to remaining daily quota
    schedule_at = data.get("schedule_at")  # ISO datetime → schedule for later

    try:
        # Route through a first-class Task so the run is visible in Activity and
        # schedulable, instead of a raw Celery dispatch.
        from backend.services.website_jobs.job_service import queue_index_run

        payload = queue_index_run(
            website_id,
            max_n=max_n,
            sync_first=sync_first,
            created_by="search_console",
            schedule_at=schedule_at,
        )
        from backend.services import google_indexing_service as gis

        payload["status"] = gis.get_status(website_id)
        payload.setdefault(
            "message",
            "Submission job queued; URLs will be submitted in the background.",
        )
        payload["queued"] = True
        return jsonify(payload), 202
    except Exception as e:
        logger.error(
            "submit dispatch failed for site %s: %s", website_id, e, exc_info=True
        )
        return (
            jsonify(
                {
                    "error": "Could not queue submission job (is the Celery worker running?)",
                    "details": str(e),
                }
            ),
            503,
        )


@search_console_bp.route("/<int:website_id>/config", methods=["POST"])
def config_route(website_id):
    """Update per-site config: enabled (auto-drip), daily_cap, notification_type."""
    site, err = _require_website(website_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        from backend.services import google_indexing_service as gis

        updated = gis.update_config(
            website_id,
            enabled=data.get("enabled"),
            daily_cap=data.get("daily_cap"),
            notification_type=data.get("notification_type"),
        )
        return jsonify({"success": True, "config": updated}), 200
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error("config DB error for site %s: %s", website_id, e, exc_info=True)
        return jsonify({"error": "Database error", "details": str(e)}), 500
    except Exception as e:
        db.session.rollback()
        logger.error("config error for site %s: %s", website_id, e, exc_info=True)
        return jsonify({"error": "Unexpected error", "details": str(e)}), 500


@search_console_bp.route("/<int:website_id>/submissions", methods=["GET"])
def submissions_route(website_id):
    """Recent submission log rows for a site (newest first)."""
    site, err = _require_website(website_id)
    if err:
        return err
    status_filter = request.args.get("status")
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except ValueError:
        limit = 100
    try:
        query = db.session.query(GoogleIndexingSubmission).filter_by(
            website_id=website_id
        )
        if status_filter:
            query = query.filter_by(status=status_filter)
        rows = (
            query.order_by(GoogleIndexingSubmission.id.desc()).limit(limit).all()
        )
        return jsonify([r.to_dict() for r in rows]), 200
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(
            "submissions DB error for site %s: %s", website_id, e, exc_info=True
        )
        return jsonify({"error": "Database error", "details": str(e)}), 500
