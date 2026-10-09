"""Tools a person has said may run in chat without asking each time.

Chosen with "Always approve" on a tool's approval card, or "Always create
without asking" on the chat page's file card, and withdrawn on the Approvals
page. Stored as a JSON list in the ``settings`` table. Consent-gated tools
(a real person's likeness) never join it: consent belongs to the reference
image, not the tool.
"""

import json
import logging
import re
from typing import Iterable, List, Set

logger = logging.getLogger(__name__)

SETTING_KEY = "chat_tools_always_approved"

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def consent_gated(name: str) -> bool:
    try:
        from backend.services.agent_tools import get_tool_registry
        tool = get_tool_registry().get_tool(name)
    except Exception:
        return False
    return bool(tool and getattr(tool, "consent_gate", False))


def _clean(names: Iterable) -> List[str]:
    out: List[str] = []
    for name in names or []:
        name = str(name or "").strip()
        if _TOOL_NAME_RE.match(name) and not consent_gated(name) and name not in out:
            out.append(name)
    return sorted(out)


def always_approved() -> Set[str]:
    """The stored names; empty when none are stored or the store is unreadable."""
    from backend.utils.settings_utils import get_setting
    raw = get_setting(SETTING_KEY, default="")
    if not raw:
        return set()
    try:
        names = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("%s is not a JSON list; ignoring it", SETTING_KEY)
        return set()
    return set(_clean(names if isinstance(names, list) else []))


def set_always_approved(names: Iterable) -> List[str]:
    """Replace the list; returns what was stored."""
    from backend.utils.settings_utils import save_setting
    cleaned = _clean(names)
    save_setting(SETTING_KEY, json.dumps(cleaned))
    return cleaned


def allow_always(names: Iterable) -> List[str]:
    return set_always_approved(always_approved() | set(_clean(names)))


def ask_again(names: Iterable) -> List[str]:
    return set_always_approved(always_approved() - {str(n) for n in names or []})
