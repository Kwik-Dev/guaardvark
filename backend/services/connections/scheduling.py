"""When a scheduled publish goes out, and who sends it.

A publish with ``scheduled_at`` waits for a person's approval like any other.
Once approved, it goes one of two ways:

- native: the platform holds it and publishes it at that time
  (``Capabilities.native_scheduling``). The hand-off happens right after the
  approval, so the post goes out even if this machine is off by then.
- timer: it waits here as ``scheduled`` until the beat task
  ``connections.publish_due_tick`` sends it (``scheduler.tick``). The timer is
  only as punctual as the worker that runs it: a publish that comes due while
  a long render holds the worker goes out when the render ends.

Times are stored as naive local datetimes, like every other column here.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

MODE_NATIVE = "native"
MODE_TIMER = "timer"

# The timer task runs once a minute, so a closer time cannot be honoured.
MIN_LEAD_SECONDS = 60
# Room between an approval and the hand-off reaching the platform: the
# publish waits in the worker queue first. Added to the platform's own minimum.
HANDOFF_MARGIN_SECONDS = 120
# A timer publish this late waits for a person instead of going out. A long
# render can hold the worker for a couple of hours in normal use; beyond six
# the machine was off or the queue stuck, and a post that late may no longer
# fit what it announces.
LATE_HOLD_SECONDS = 6 * 3600


def parse_when(value) -> Optional[datetime]:
    """An ISO 8601 time from a request, as a naive local datetime.

    A time with an offset (``Z`` or ``+02:00``) is converted to local time; one
    without is taken as local already. Empty means "now"; anything else that
    does not parse raises ValueError.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as e:
            raise ValueError(f"Could not read the scheduled time '{value}'.") from e
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def to_utc_iso(value: Optional[datetime]) -> Optional[str]:
    """A stored naive local time as an unambiguous ISO string with its offset."""
    if value is None:
        return None
    return value.astimezone().isoformat()


def problems(scheduled_at: Optional[datetime], now: Optional[datetime] = None) -> List[str]:
    """Why a requested time cannot be scheduled; empty when it can."""
    if scheduled_at is None:
        return []
    now = now or datetime.now()
    if scheduled_at < now + timedelta(seconds=MIN_LEAD_SECONDS):
        return ["Pick a time at least a minute from now, or publish now."]
    return []


def plan(capabilities, scheduled_at: datetime, visibility: Optional[str], now: Optional[datetime] = None) -> str:
    """Native when the platform can hold this post for that time, else the timer."""
    native = getattr(capabilities, "native_scheduling", None)
    if native is None:
        return MODE_TIMER
    if native.visibilities and (visibility or "") not in native.visibilities:
        return MODE_TIMER
    now = now or datetime.now()
    lead = (scheduled_at - now).total_seconds()
    if lead < native.min_lead_seconds + HANDOFF_MARGIN_SECONDS:
        return MODE_TIMER
    return MODE_NATIVE


def can_hand_off(capabilities, scheduled_at: datetime, now: Optional[datetime] = None) -> bool:
    """At send time: whether the platform's minimum lead still holds."""
    native = getattr(capabilities, "native_scheduling", None)
    if native is None:
        return False
    now = now or datetime.now()
    return (scheduled_at - now).total_seconds() >= native.min_lead_seconds


def describe(capabilities, label: str, mode: str) -> str:
    """One sentence for the composer on who publishes it."""
    if mode == MODE_NATIVE:
        field = capabilities.native_scheduling.field
        return f"{label} holds it and publishes it at that time ({field})."
    return "Guaardvark publishes it at that time, so it needs to be running then."
