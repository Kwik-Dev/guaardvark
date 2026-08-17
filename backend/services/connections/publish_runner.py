"""Execute a queued publish.

Called from the unified task executor. Claims the record before any network
call and refuses to re-post one that already has a remote id, so a redelivered
Celery message cannot produce a duplicate post.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Callable, Dict

from backend.services.connections import gates, media as media_util, registry, service
from backend.services.connections.base import PublishRequest

logger = logging.getLogger(__name__)


def run(task: Dict[str, Any], update_progress: Callable[[int, str], None]) -> Dict[str, Any]:
    """Publish the record referenced by *task*. Returns a result summary."""
    from backend.models import Connection, PublishRecord, db

    config = task.get("workflow_config") or {}
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except (ValueError, TypeError):
            config = {}

    record_id = config.get("publish_record_id")
    if not record_id:
        return {"error": "No publish_record_id on the task."}

    record = PublishRecord.query.get(record_id)
    if record is None:
        return {"error": f"Publish record {record_id} not found."}

    if record.remote_id:
        logger.info("Publish record %s already posted; skipping.", record_id)
        return {"skipped": True, "remote_url": record.remote_url}
    if record.status in ("cancelled", "rejected"):
        return {"skipped": True, "status": record.status}

    connection = Connection.query.get(record.connection_id)
    if connection is None:
        return _fail(record, "The target connection no longer exists.")

    allowed, reason = gates.check_can_publish(connection.provider)
    if not allowed:
        return _fail(record, reason or "Blocked by a safety gate.")

    # Claim before any network call so a concurrent worker cannot double-post.
    record.status = "processing"
    record.attempt_count = (record.attempt_count or 0) + 1
    db.session.commit()

    update_progress(10, f"Publishing to {connection.provider}…")

    try:
        spec = registry.spec_for(connection.provider)
        module = registry.get_provider(connection.provider)
        ctx = service.build_ctx(connection)
        request = PublishRequest(
            record_id=record.id,
            connection_id=connection.id,
            body=record.body or "",
            title=record.title,
            link_url=record.link_url,
            tags=_json_list(record.tags),
            visibility=record.visibility or spec.capabilities.default_visibility,
            media=media_util.media_from_refs(record.media_refs),
            config=ctx.config,
        )
        result = module.publish(ctx, request, update_progress)
    except Exception as e:  # noqa: BLE001 - any provider failure becomes a failed record
        logger.warning("Publish %s raised: %s", record_id, e, exc_info=True)
        return _fail(record, service.redact(str(e)))

    if not result.ok:
        return _fail(record, service.redact(result.message))

    record.status = "posted"
    record.remote_id = result.remote_id
    record.remote_url = result.remote_url
    record.error_message = None
    record.posted_at = datetime.now()
    connection.last_used_at = datetime.now()
    db.session.commit()

    gates.record_publish(connection.provider)
    update_progress(100, "Published.")
    return {
        "published": True,
        "platform": connection.provider,
        "remote_id": result.remote_id,
        "remote_url": result.remote_url,
    }


def _fail(record, message: str) -> Dict[str, Any]:
    from backend.models import db

    record.status = "failed"
    record.error_message = message
    db.session.commit()
    return {"error": message}


def _json_list(raw) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []
