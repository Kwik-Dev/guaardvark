"""Safety gates for publishing.

Publishing your own asset is a different act from unsolicited outreach, so it
gets its own enable switch and defaults to on. Each publish waits on the
Approvals page for a person. The one exception is a connection that can post
only into a channel its owner manages (``ProviderSpec.posts_to_owned_channel``,
a Discord webhook) and that the person switched to post without approval;
even then a request from chat, MCP or a schedule waits. The two paths share
one per-platform cadence budget and one stop on all public posting
(``kill_switch.posting_stop_reason``). That stop is its own setting, not the
outreach on/off switch: outreach defaults off, and that must not hold back a
publish.

Some visibilities are held back per connection until its person turns them
on (``Capabilities.public_opt_in``: YouTube uploads stay private by default).
``visibility_refusal`` says why such a post is refused while the switch is off.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

PUBLISH_ENABLED_KEY = "connections_publish_enabled"
# Connection.config key a person sets on an owned-channel connection.
POST_WITHOUT_APPROVAL_KEY = "post_without_approval"
# Connection.config key for a provider's public opt-in (Capabilities.public_opt_in).
ALLOW_PUBLIC_KEY = "allow_public"

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


def may_skip_approval(provider: str | None) -> bool:
    """Whether a connection of ``provider`` may be switched to post without a
    per-post approval: only one that posts into a channel its owner manages."""
    try:
        from backend.services.connections import registry

        return bool(registry.spec_for(provider or "").posts_to_owned_channel)
    except Exception:  # noqa: BLE001 - an unknown provider waits for a person
        return False


def posts_without_approval(connection) -> bool:
    """True only for an owned-channel connection whose person switched it to
    post without approval. A config that cannot be read means approval."""
    if connection is None or not may_skip_approval(getattr(connection, "provider", None)):
        return False
    try:
        raw = connection.config
        config = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return isinstance(config, dict) and config.get(POST_WITHOUT_APPROVAL_KEY) is True
    except (ValueError, TypeError):
        return False


def _config_dict(config) -> dict:
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except (ValueError, TypeError):
            return {}
    return config if isinstance(config, dict) else {}


def _public_opt_in(provider: str | None):
    try:
        from backend.services.connections import registry

        return registry.spec_for(provider or "").capabilities.public_opt_in
    except Exception:  # noqa: BLE001 - an unknown provider cannot be published to anyway
        return None


def public_opt_in_state(provider: str | None, config) -> Optional[Dict[str, Any]]:
    """Whether a connection may post with the visibilities its provider holds
    back (``Capabilities.public_opt_in``); None when the provider holds none.

    ``config`` is the connection's config (JSON text or a dict). Returns
    {"on": bool, "source": ...}: "connection" for the person's choice on the
    connection's card; "setting" for the provider's install-wide legacy
    setting, read only while the connection has no choice stored; "default"
    (off) otherwise. Anything but a stored true or false counts as no choice.
    """
    opt_in = _public_opt_in(provider)
    if opt_in is None:
        return None
    stored = _config_dict(config).get(ALLOW_PUBLIC_KEY)
    if isinstance(stored, bool):
        return {"on": stored, "source": "connection"}
    if opt_in.legacy_setting and _setting(opt_in.legacy_setting, "false") in _TRUTHY:
        return {"on": True, "source": "setting"}
    return {"on": False, "source": "default"}


def visibility_refusal(provider: str | None, config, visibility: str | None) -> Optional[str]:
    """Why a post with ``visibility`` is refused on this connection, or None.
    Pass the visibility the post is sent with, its default already applied."""
    opt_in = _public_opt_in(provider)
    if opt_in is None or (visibility or "") not in opt_in.visibilities:
        return None
    state = public_opt_in_state(provider, config)
    return None if state and state["on"] else opt_in.refusal


def requires_approval(requested_by: str, connection=None) -> bool:
    """Whether this publish waits on the Approvals page.

    Always, except a publish a person started from the app ("ui") to an
    owned-channel connection set to post without approval. An agent-initiated
    publish is never allowed to go out unattended.
    """
    if (requested_by or "ui").strip().lower() in ALWAYS_SUPERVISED_SOURCES:
        return True
    return not posts_without_approval(connection)


def posting_stop_reason() -> Optional[str]:
    """Why public posting is stopped, or None. A stop that cannot be checked refuses."""
    try:
        from backend.services.social_outreach import kill_switch

        return kill_switch.posting_stop_reason()
    except Exception as e:  # noqa: BLE001 - an unknown stop state means fail closed
        logger.warning("gates: could not check the posting stop: %s", e)
        return "The public posting stop could not be checked; refusing to publish."


def check_can_publish(platform: str) -> Tuple[bool, Optional[str]]:
    """Gate a publish at both queue and execution time."""
    stopped = posting_stop_reason()
    if stopped:
        return False, stopped

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
