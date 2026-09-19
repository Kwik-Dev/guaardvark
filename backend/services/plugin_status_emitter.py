"""Push plugin list snapshots over Socket.IO instead of HTTP polling."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PLUGINS_STATUS_ROOM = "plugins_status"


def build_plugins_snapshot(reason: str = "", *, manager=None) -> Dict[str, Any]:
    """Snapshot of ``manager``'s plugins; the process singleton when none is given.

    The manager that broadcasts must pass itself: constructing the singleton
    from inside another manager's __init__ built a second, real-registry
    manager (probing every install port and running orphan cleanup) whenever
    a test or a tool built a private one.
    """
    if manager is None:
        from backend.plugins.plugin_manager import get_plugin_manager
        manager = get_plugin_manager()

    try:
        from backend.services.plugin_bridge import get_orchestrator_state
        orchestrator = get_orchestrator_state()
    except Exception:
        orchestrator = {}

    plugins = manager.list_plugins()
    return {
        "plugins": plugins,
        "count": len(plugins),
        "orchestrator": orchestrator,
        "reason": reason,
        "timestamp": time.time(),
    }


def emit_plugins_snapshot(reason: str = "", *, to_sid: Optional[str] = None, manager=None) -> None:
    """Broadcast (or unicast) the current plugin list to subscribed clients."""
    try:
        from backend.socketio_instance import socketio

        payload = build_plugins_snapshot(reason, manager=manager)
        if to_sid:
            socketio.emit("plugins:status", payload, to=to_sid)
        else:
            socketio.emit("plugins:status", payload, room=PLUGINS_STATUS_ROOM)
    except Exception as e:
        logger.debug("plugins:status emit skipped: %s", e)