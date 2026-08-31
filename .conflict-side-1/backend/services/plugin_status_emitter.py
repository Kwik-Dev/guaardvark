"""Push plugin list snapshots over Socket.IO instead of HTTP polling."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PLUGINS_STATUS_ROOM = "plugins_status"


def build_plugins_snapshot(reason: str = "") -> Dict[str, Any]:
    from backend.plugins.plugin_manager import get_plugin_manager

    try:
        from backend.services.plugin_bridge import get_orchestrator_state
        orchestrator = get_orchestrator_state()
    except Exception:
        orchestrator = {}

    plugins = get_plugin_manager().list_plugins()
    return {
        "plugins": plugins,
        "count": len(plugins),
        "orchestrator": orchestrator,
        "reason": reason,
        "timestamp": time.time(),
    }


def emit_plugins_snapshot(reason: str = "", *, to_sid: Optional[str] = None) -> None:
    """Broadcast (or unicast) the current plugin list to subscribed clients."""
    try:
        from backend.socketio_instance import socketio

        payload = build_plugins_snapshot(reason)
        if to_sid:
            socketio.emit("plugins:status", payload, to=to_sid)
        else:
            socketio.emit("plugins:status", payload, room=PLUGINS_STATUS_ROOM)
    except Exception as e:
        logger.debug("plugins:status emit skipped: %s", e)