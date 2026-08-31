"""Safety gates for publishing.

Publishing your own asset is a different act from unsolicited outreach, so it
gets its own enable switch and defaults to on. The two paths still share one
per-platform cadence budget, and the outreach kill switch stops both.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

PUBLISH_ENABLED_KEY = "connections_publish_enabled"
PUBLISH_SUPERVISED_KEY = "connections_publish_supervised"

# Requests from an agent always require a human click, regardless of settings.
# "unknown" is here so an unattributed request fails safe: a caller that omits
# its source is supervised rather than silently treated as a human click.
ALWAYS_SUPERVISED_SOURCES = ("chat", "mcp", "schedule", "unknown")

# Sources a caller may claim for itself. Anything else becomes "unknown", so a
# typo or a new agent path cannot land on the unsupervised branch by accident.
KNOWN_SOURCES = ("ui", "chat", "mcp", "schedule")


def normalize_source(requested_by: str | None) -> str:
    """Map a caller-supplied source onto a known one, defaulting to supervised.

    The value arrives in a request body and is not authenticated, so it is a
    claim rather than a fact. Claiming an agent source only ever increases
    supervision, which is safe; anything unrecognised is treated as unknown.
    """
    candidate = (requested_by or "").strip().lower()
    return candidate if candidate in KNOWN_SOURCES else "unknown"

_TRUTHY = ("1", "true", "yes", "on")


def _setting(key: str, default: str) -> str:
    try:
        from backend.utils.settings_utils import get_setting

        return (get_setting(key, default) or default).strip().lower()
    except Exception as e:  # noqa: BLE001
        logger.debug("gates: could not read %s (%s); using default.", key, e)
        return default


def publish_enabled() -> bool:
    return _setting(PUBLISH_ENABLED_KEY, "true") in _TRUTHY


def set_publish_enabled(value: bool) -> None:
    from backend.utils.settings_utils import save_setting

    save_setting(PUBLISH_ENABLED_KEY, "true" if value else "false")


def publish_supervised() -> bool:
    return _setting(PUBLISH_SUPERVISED_KEY, "false") in _TRUTHY


def set_publish_supervised(value: bool) -> None:
    from backend.utils.settings_utils import save_setting

    save_setting(PUBLISH_SUPERVISED_KEY, "true" if value else "false")


def requires_approval(requested_by: str) -> bool:
    """An agent-initiated publish is never allowed to go out unattended."""
    return (requested_by or "ui").strip().lower() in ALWAYS_SUPERVISED_SOURCES or publish_supervised()


def check_can_publish(platform: str) -> Tuple[bool, Optional[str]]:
    """Gate a publish at both queue and execution time."""
    if not publish_enabled():
        return False, "Publishing is disabled."

    try:
        from backend.services.social_outreach import kill_switch

        allowed, reason = kill_switch.cadence_allows_post(platform)
        if not allowed:
            return False, reason or "Rate limit reached for this platform."
    except Exception as e:  # noqa: BLE001 - cadence backend down means fail closed
        logger.warning("gates: cadence check failed for %s: %s", platform, e)
        return False, "Rate-limit check unavailable; refusing to publish."

    return True, None


def record_publish(platform: str) -> None:
    """Charge a successful post against the shared per-platform budget."""
    try:
        from backend.services.social_outreach import kill_switch

        kill_switch.record_post(platform)
    except Exception as e:  # noqa: BLE001 - bookkeeping must not fail the publish
        logger.warning("gates: could not record cadence for %s: %s", platform, e)
