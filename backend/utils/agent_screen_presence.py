"""Whether someone has the Agent Screen open in the app right now.

The Agent Screen viewer (frontend AgentScreenViewer, and its pop-out window)
fetches a frame from POST /api/agent-control/capture/raw at least once a
second while it is open and streaming; nothing else calls that route. Each
frame served stamps the time in Redis, so the backend and the Celery worker
read the same answer. A paused, collapsed or closed viewer stops the frames.

This says a viewer is streaming, not that a person is looking at it; the
watched outreach post pairs it with the person's own "Post now, I'm watching"
click. Without Redis nobody counts as watching.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

FRAME_KEY = "agent_screen:viewer_frame_at"
# The viewer asks for a frame every 1/fps seconds (1 to 10 fps). A capture can
# take a few hundred milliseconds on a busy machine; ten seconds without one
# means the viewer is closed, paused or collapsed.
WATCH_WINDOW_S = 10.0
_KEY_TTL_S = 60
# After Redis fails, frames skip it for this long instead of waiting on a
# connect timeout each time.
_RETRY_AFTER_S = 10.0

NOT_WATCHING = (
    "The Agent Screen is not open in the app. Open it from the sidebar, keep it "
    "streaming, and press Post now again."
)
UNKNOWN = "Could not tell whether the Agent Screen is open (Redis unavailable); nothing was posted."

_client = None
_down_until = 0.0


def _redis():
    global _client
    if time.monotonic() < _down_until:
        return None
    if _client is None:
        import redis

        _client = redis.Redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
            socket_timeout=0.5,
            socket_connect_timeout=0.5,
        )
    return _client


def _failed(e: Exception) -> None:
    global _down_until
    _down_until = time.monotonic() + _RETRY_AFTER_S
    logger.debug("agent screen presence: redis unavailable: %s", e)


def note_frame() -> None:
    """Record that the viewer was just served a frame. Never raises."""
    try:
        client = _redis()
        if client is not None:
            client.set(FRAME_KEY, repr(time.time()), ex=_KEY_TTL_S)
    except Exception as e:  # noqa: BLE001 - a frame must not fail on bookkeeping
        _failed(e)


def seconds_since_frame() -> Optional[float]:
    """Seconds since the viewer's last frame, or None if there is none to read."""
    client = _redis()
    if client is None:
        raise ConnectionError("redis unavailable")
    raw = client.get(FRAME_KEY)
    if not raw:
        return None
    try:
        return max(0.0, time.time() - float(raw))
    except ValueError:
        return None


def watching(window_s: float = WATCH_WINDOW_S) -> tuple[bool, Optional[str]]:
    """(True, None) when the viewer was served a frame within ``window_s``,
    else (False, why). Fails closed."""
    try:
        age = seconds_since_frame()
    except Exception as e:  # noqa: BLE001 - unknown presence means not watching
        _failed(e)
        return False, UNKNOWN
    if age is None or age > window_s:
        return False, NOT_WATCHING
    return True, None
