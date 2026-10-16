"""The timer behind scheduled publishing, run once a minute by Celery beat.

Each tick does at most two things:

1. settle: ask the platform about one natively scheduled post whose time has
   passed. Published becomes ``posted``; gone becomes ``failed``; still held
   is asked again after ``RECHECK_SECONDS``.
2. release: send at most one approved ``scheduled`` post whose time has come,
   and only when no other publish is in flight, so posts leave one at a time.
   It goes only if a person approved it, or if it needs no approval
   (``gates.requires_approval``, asked again at that moment). A post the rate
   limit or the shared cadence turns away waits for a later tick. A post
   whose time came while public posting was stopped or publishing was off,
   or that is more than ``LATE_HOLD_SECONDS`` late, goes back to Approvals
   for a person's click instead of going out late.

With nothing scheduled the beat gate (``celery_beat_gates``) holds the task
back, so a fresh install never runs it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from backend.services.connections import gates, publish_service, rate_limits, registry, scheduling
from backend.services.connections.base import SCHEDULE_GONE, SCHEDULE_PUBLISHED

logger = logging.getLogger(__name__)

# How long after its time a natively scheduled post is first looked up, and
# how often after that while the platform still holds it.
SETTLE_DELAY_SECONDS = 60
RECHECK_SECONDS = 5 * 60

STOPPED_DUE_MESSAGE = (
    "Its time came while public posting was stopped or publishing was off. "
    "Approve it to send it now, or reject it."
)
NOT_APPROVED_MESSAGE = "Its time came without a person's approval. Approve it to send it."


def _late_message(late: timedelta) -> str:
    hours = int(late.total_seconds() // 3600)
    return (
        f"It missed its time by about {hours} hours (Guaardvark or its worker was not "
        "running). Approve it to send it now, or reject it."
    )


def tick(now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now()
    out: Dict[str, Any] = {"settled": settle_one(now)}
    out.update(release_due(now))
    return out


def settle_one(now: datetime) -> Optional[Dict[str, Any]]:
    """Look up one natively scheduled post whose time has passed."""
    from backend.models import Connection, PublishRecord, db
    from backend.services.connections import service

    record = (
        PublishRecord.query.filter(
            PublishRecord.status == "scheduled",
            PublishRecord.scheduled_natively.is_(True),
            PublishRecord.scheduled_at <= now - timedelta(seconds=SETTLE_DELAY_SECONDS),
            PublishRecord.updated_at <= now - timedelta(seconds=RECHECK_SECONDS),
        )
        .order_by(PublishRecord.scheduled_at)
        .first()
    )
    if record is None:
        return None

    connection = Connection.query.get(record.connection_id) if record.connection_id else None
    if connection is None or not registry.supports(connection.provider, "scheduled_state"):
        # Nothing left to ask; the platform was told to publish it at this time.
        _mark_posted(record, connection, None, "Handed to the platform for this time; not confirmed.")
        return {"publish_record_id": record.id, "state": "unconfirmed"}

    try:
        check = registry.get_provider(connection.provider).scheduled_state(
            service.build_ctx(connection), record.remote_id
        )
    except Exception as e:  # noqa: BLE001 - a failed lookup is retried, never fatal
        logger.warning("scheduler: lookup of publish %s raised: %s", record.id, e)
        record.updated_at = now
        db.session.commit()
        return {"publish_record_id": record.id, "state": "unknown"}

    if check.state == SCHEDULE_PUBLISHED:
        _mark_posted(record, connection, check.remote_url, check.message or None)
    elif check.state == SCHEDULE_GONE:
        record.status = "failed"
        record.error_message = check.message or "The platform no longer has this post."
        db.session.commit()
    else:
        record.updated_at = now
        if check.message:
            record.error_message = check.message
        db.session.commit()
    return {"publish_record_id": record.id, "state": check.state}


def _mark_posted(record, connection, remote_url: Optional[str], note: Optional[str]) -> None:
    from backend.models import db

    record.status = "posted"
    record.posted_at = record.scheduled_at
    if remote_url:
        record.remote_url = remote_url
    record.error_message = note
    db.session.commit()
    if connection is not None:
        # Charged now that it is public: the cadence outreach and publishing share.
        gates.record_publish(connection.provider)


def _in_flight(now: datetime) -> bool:
    from backend.models import PublishRecord

    cutoff = now - timedelta(seconds=publish_service.PUBLISH_TIME_LIMIT)
    return (
        PublishRecord.query.filter(
            PublishRecord.status.in_(rate_limits.IN_FLIGHT_STATUSES),
            PublishRecord.updated_at >= cutoff,
        ).count()
        > 0
    )


def release_due(now: datetime) -> Dict[str, Any]:
    """Hold what may not go out, then send the first due post that may."""
    from backend.models import Connection, PublishRecord

    due = (
        PublishRecord.query.filter(
            PublishRecord.status == "scheduled",
            PublishRecord.scheduled_natively.is_(False),
            PublishRecord.scheduled_at <= now,
        )
        .order_by(PublishRecord.scheduled_at)
        .all()
    )
    out: Dict[str, Any] = {"released": None, "held": [], "waiting": []}
    if not due:
        return out

    posting_blocked = gates.posting_stop_reason() is not None or not gates.publish_enabled()
    busy = _in_flight(now)

    for record in due:
        connection = Connection.query.get(record.connection_id) if record.connection_id else None
        if connection is None:
            publish_service.hold(record, "The connection it was scheduled for no longer exists.")
            out["held"].append(record.id)
            continue
        if not publish_service.approved(record, connection):
            publish_service.hold(record, NOT_APPROVED_MESSAGE)
            out["held"].append(record.id)
            continue
        if posting_blocked:
            publish_service.hold(record, STOPPED_DUE_MESSAGE)
            out["held"].append(record.id)
            continue
        late = now - record.scheduled_at
        if late.total_seconds() > scheduling.LATE_HOLD_SECONDS:
            publish_service.hold(record, _late_message(late))
            out["held"].append(record.id)
            continue
        if busy or out["released"] is not None:
            out["waiting"].append(record.id)
            continue

        try:
            spec = registry.spec_for(connection.provider)
        except KeyError:
            publish_service.hold(record, f"Provider '{connection.provider}' is not available.")
            out["held"].append(record.id)
            continue

        allowed, _reason = gates.check_can_publish(connection.provider)
        verdict = rate_limits.check(connection.id, spec.capabilities.rate_limits, now=now)
        if not allowed or not verdict.allowed:
            out["waiting"].append(record.id)
            continue

        try:
            out["released"] = publish_service.release_due(record, connection, spec, now=now)
        except RuntimeError as e:
            # A refused limit leaves it scheduled; a failed enqueue marks it failed.
            logger.warning("scheduler: could not send publish %s: %s", record.id, e)
            out["error"] = {"publish_record_id": record.id, "message": str(e)}
    return out
