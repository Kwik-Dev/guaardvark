"""Per-account publish ceilings, as each provider declares them.

``Capabilities.rate_limits`` holds the ceilings; this module counts against
them from ``publish_records``, the durable record of what went out, so the
check needs no Redis and survives a restart. A post counts at the moment it
goes, or went, public:

- posted, and posted then deleted: ``posted_at``
- being sent (queued, processing): now
- held by the platform for later (native scheduling): ``scheduled_at``

A post at time ``at`` is allowed when no window of the declared length that
contains ``at`` would hold more than the limit with it. Anything that stops
the count refuses the post.

These ceilings are per account. The per-platform cadence that outreach and
publishing share (``social_outreach.kill_switch``) is a separate brake and
still applies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional

from backend.services.connections.base import RateLimit

logger = logging.getLogger(__name__)

IN_FLIGHT_STATUSES = ("queued", "processing")
# Posted, and posted then taken down: the post still counted when it went out.
WENT_OUT_STATUSES = ("posted", "deleted")
UNCHECKABLE_REASON = "The publish rate limit could not be checked; refusing to publish."


@dataclass
class Verdict:
    allowed: bool
    reason: Optional[str] = None
    # Earliest time the limit that refused is expected to allow a post.
    retry_at: Optional[datetime] = None


def describe_window(seconds: int) -> str:
    if seconds % 86400 == 0:
        days = seconds // 86400
        return "day" if days == 1 else f"{days} days"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return "hour" if hours == 1 else f"{hours} hours"
    minutes = max(1, seconds // 60)
    return f"{minutes} minutes"


def _in_flight_cutoff(now: datetime) -> datetime:
    """A queued or processing row older than the publish hard limit is not in flight."""
    from backend.services.connections.publish_service import PUBLISH_TIME_LIMIT

    return now - timedelta(seconds=PUBLISH_TIME_LIMIT)


def event_times(
    connection_id: int,
    lo: datetime,
    hi: datetime,
    *,
    now: datetime,
    exclude_record_id: Optional[int] = None,
) -> List[datetime]:
    """When this connection's posts go or went public, between ``lo`` and ``hi``."""
    from backend.models import PublishRecord

    base = PublishRecord.query.filter(PublishRecord.connection_id == connection_id)
    if exclude_record_id is not None:
        base = base.filter(PublishRecord.id != exclude_record_id)

    times: List[datetime] = []
    went_out = base.filter(
        PublishRecord.status.in_(WENT_OUT_STATUSES),
        PublishRecord.posted_at.isnot(None),
        PublishRecord.posted_at > lo,
        PublishRecord.posted_at < hi,
    ).with_entities(PublishRecord.posted_at)
    times.extend(row[0] for row in went_out)

    if lo < now < hi:
        in_flight = base.filter(
            PublishRecord.status.in_(IN_FLIGHT_STATUSES),
            PublishRecord.updated_at >= _in_flight_cutoff(now),
        ).count()
        times.extend([now] * in_flight)

    held = base.filter(
        PublishRecord.status == "scheduled",
        PublishRecord.scheduled_natively.is_(True),
        PublishRecord.scheduled_at > lo,
        PublishRecord.scheduled_at < hi,
    ).with_entities(PublishRecord.scheduled_at)
    times.extend(row[0] for row in held)
    return sorted(times)


def _busiest_window(times: Iterable[datetime], at: datetime, window: timedelta) -> List[datetime]:
    """The posts in the busiest window of length ``window`` that contains ``at``.

    Such a window starts in (at - window, at]. The busiest one can be taken to
    start just after ``at - window`` (it then holds the posts up to ``at``) or
    at one of the posts.
    """
    times = sorted(t for t in times if at - window < t < at + window)
    best = [t for t in times if t <= at]
    for start in (t for t in times if t <= at):
        inside = [t for t in times if start <= t < start + window]
        if len(inside) > len(best):
            best = inside
    return best


def check(
    connection_id: int,
    limits: Iterable[RateLimit],
    *,
    at: Optional[datetime] = None,
    now: Optional[datetime] = None,
    exclude_record_id: Optional[int] = None,
) -> Verdict:
    """Whether one more post through this connection at ``at`` stays within every limit."""
    limits = list(limits or ())
    if not limits:
        return Verdict(allowed=True)
    now = now or datetime.now()
    at = at or now
    try:
        widest = timedelta(seconds=max(limit.window_seconds for limit in limits))
        times = event_times(
            connection_id, at - widest, at + widest, now=now, exclude_record_id=exclude_record_id
        )
        for limit in limits:
            window = timedelta(seconds=limit.window_seconds)
            busiest = _busiest_window(times, at, window)
            if len(busiest) + 1 > limit.posts:
                retry_at = busiest[-limit.posts] + window if len(busiest) >= limit.posts else None
                when = f" Next opening about {retry_at:%H:%M}." if retry_at and retry_at > now else ""
                return Verdict(
                    allowed=False,
                    reason=(
                        f"This account's limit is {limit.posts} posts per "
                        f"{describe_window(limit.window_seconds)}, and that many already "
                        f"count against it.{when}"
                    ),
                    retry_at=retry_at,
                )
    except Exception as e:  # noqa: BLE001 - an uncounted limit must refuse, not wave through
        logger.warning("rate_limits: check failed for connection %s: %s", connection_id, e)
        return Verdict(allowed=False, reason=UNCHECKABLE_REASON)
    return Verdict(allowed=True)


def usage(connection_id: int, limits: Iterable[RateLimit], now: Optional[datetime] = None) -> List[Dict]:
    """Posts counted in each declared window ending now, for the Social Accounts page."""
    limits = list(limits or ())
    if not limits:
        return []
    now = now or datetime.now()
    widest = timedelta(seconds=max(limit.window_seconds for limit in limits))
    times = event_times(connection_id, now - widest, now + timedelta(seconds=1), now=now)
    out = []
    for limit in limits:
        since = now - timedelta(seconds=limit.window_seconds)
        out.append({
            **limit.to_dict(),
            "window": describe_window(limit.window_seconds),
            "used": sum(1 for t in times if since < t <= now),
        })
    return out
