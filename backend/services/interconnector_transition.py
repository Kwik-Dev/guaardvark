"""Clients that joined the fleet before machines had their own keys.

Before enrolment (backend/utils/node_auth.py) a client joined with the shared
fleet key alone and signed nothing. Such a client is usually updated through
the Interconnector itself, from its master, so once the master runs the new
code it must still answer that client's unsigned requests long enough for the
client to fetch the new code, restart and enrol. This module is that
transition window, on the master.

A legacy client is a node row in client mode with no enrolment record
(InterconnectorNodeIdentity): a machine that registered under the old rule and
has not enrolled since. Its unsigned request is taken on the old rule (the
fleet key, when the master requires one; the route checks that) when:

- the window is open, and
- the request comes from the address on record for that machine, or, for a
  heartbeat, names it in the URL. A heartbeat the client's own backend sends
  from a new address moves the record there, so a client whose lease changed
  is not stranded. One sent by a browser page (it carries an Origin) only
  keeps the machine alive: the old Settings page heartbeats from whatever
  computer the page is open on.

The window opens only when the operator opens it (Settings → Interconnector,
POST /api/interconnector/transition), for WINDOW_HOURS by default. It closes at
that deadline, or when the operator closes it; the operator can open it again
for a client that was offline. Until it is opened, a legacy client's unsigned
requests are refused. A machine that has enrolled
is no longer a legacy client, so its unsigned requests are refused while the
window is still open. Every request taken on the old rule, and every one
refused after the window closed, is logged as a warning (at most once per
machine and route every LOG_EVERY_S seconds; the rest at debug).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SETTING_KEY = "interconnector_transition"
WINDOW_HOURS = 48
MAX_WINDOW_HOURS = 7 * 24
LOG_EVERY_S = 600

_log_lock = threading.Lock()
_last_logged: Dict[Tuple[str, str, str], float] = {}


def _now(now: Optional[float]) -> float:
    return time.time() if now is None else float(now)


def _iso(epoch: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(epoch).isoformat(timespec="seconds") if epoch else None


def host_only(value: Optional[str]) -> str:
    """The address part of a request address or a stored node host, lower
    case, without scheme, port or IPv6 brackets; "" when there is none."""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower().startswith("::ffff:"):
        text = text[7:]
    try:
        return str(ipaddress.ip_address(text.split("%", 1)[0])).lower()
    except ValueError:
        pass
    if "://" not in text:
        text = f"http://{text}"
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        return ""
    return host.lower()


# ---- state --------------------------------------------------------------------

def read_state() -> dict:
    """The window as recorded on this master: {} until it first opens."""
    from backend.models import Setting, db

    row = db.session.get(Setting, SETTING_KEY)
    if row is None or not row.value:
        return {}
    try:
        state = json.loads(row.value)
    except ValueError:
        return {}
    return state if isinstance(state, dict) else {}


def _write_state(state: dict) -> None:
    from backend.models import Setting, db

    row = db.session.get(Setting, SETTING_KEY)
    if row is None:
        db.session.add(Setting(key=SETTING_KEY, value=json.dumps(state)))
    else:
        row.value = json.dumps(state)
    db.session.commit()


def window_open(state: dict, now: Optional[float] = None) -> bool:
    return bool(state) and not state.get("closed_at") and float(state.get("until") or 0) > _now(now)


def open_window(hours: float = WINDOW_HOURS, opened_by: str = "operator",
                now: Optional[float] = None) -> dict:
    """Open the window for `hours` from now (at most MAX_WINDOW_HOURS)."""
    hours = max(0.0, min(float(hours), float(MAX_WINDOW_HOURS)))
    current = _now(now)
    state = {"opened_at": current, "until": current + hours * 3600, "opened_by": opened_by,
             "closed_at": None, "closed_by": None}
    _write_state(state)
    logger.warning(
        "[SYNC] Transition window open until %s (%s): machines that joined before enrolment "
        "may sync on the fleet key alone until then",
        _iso(state["until"]), opened_by,
    )
    return state


def close_window(closed_by: str = "operator", now: Optional[float] = None) -> dict:
    state = read_state() or {"opened_at": None, "until": None, "opened_by": None}
    state.update({"closed_at": _now(now), "closed_by": closed_by})
    _write_state(state)
    logger.warning("[SYNC] Transition window closed (%s)", closed_by)
    return state


# ---- legacy clients -------------------------------------------------------------

def _enrolled_ids() -> set:
    from backend.models import InterconnectorNodeIdentity, db

    return {node_id for (node_id,) in db.session.query(InterconnectorNodeIdentity.node_id)}


def legacy_machines() -> List:
    """Client node rows that have not enrolled with a key of their own."""
    from backend.models import InterconnectorNode, db
    from backend.utils.node_identity import local_node_id

    enrolled = _enrolled_ids()
    me = local_node_id()
    return [
        node for node in db.session.query(InterconnectorNode).filter(
            InterconnectorNode.node_mode == "client"
        ).all()
        if node.node_id not in enrolled and node.node_id != me
    ]


def status(now: Optional[float] = None) -> dict:
    """What the master's Interconnector page shows about the window."""
    state = read_state()
    return {
        "open": window_open(state, now),
        "opened_at": _iso(state.get("opened_at")),
        "until": _iso(state.get("until")),
        "opened_by": state.get("opened_by"),
        "closed_at": _iso(state.get("closed_at")),
        "window_hours": WINDOW_HOURS,
        "max_window_hours": MAX_WINDOW_HOURS,
        "legacy_machines": [
            {"node_id": node.node_id, "node_name": node.node_name, "host": node.host,
             "last_heartbeat": node.last_heartbeat.isoformat() if node.last_heartbeat else None}
            for node in legacy_machines()
        ],
    }


def _log(kind: str, node_id: str, path: str, message: str, *args) -> None:
    key = (kind, node_id, path)
    current = time.monotonic()
    with _log_lock:
        last = _last_logged.get(key)
        loud = last is None or current - last >= LOG_EVERY_S
        if loud:
            _last_logged[key] = current
    (logger.warning if loud else logger.debug)(message, *args)


def legacy_caller(address: str, path: str, url_node_id: Optional[str] = None,
                  may_move: bool = False, now: Optional[float] = None) -> Optional[str]:
    """The node id of the legacy client an unsigned request may be taken
    from, or None. The caller has already checked the fleet key. may_move:
    a heartbeat naming the machine may move its record to this address."""
    from backend.models import db

    address = host_only(address)
    if not address:
        return None
    candidates = legacy_machines()
    if url_node_id is not None:
        candidates = [node for node in candidates if node.node_id == url_node_id]
    else:
        candidates = [node for node in candidates if host_only(node.host) == address]
    if not candidates:
        return None
    node = candidates[0]
    label = f"{node.node_name or 'node'} ({node.node_id[:8]})"

    state = read_state()
    if not window_open(state, now):
        _log("refused", node.node_id, path,
             "[SYNC] Refused unsigned %s from %s at %s: the transition window is closed. To update "
             "that machine through the Interconnector, open the window in Settings → Interconnector "
             "on the master (Allow for %s hours)", path, label, address, WINDOW_HOURS)
        return None

    if url_node_id is not None and may_move and host_only(node.host) != address:
        logger.warning("[SYNC] Transition: %s now calls from %s (was %s); its record moves there",
                       label, address, node.host)
        node.host = address
        db.session.commit()

    _log("taken", node.node_id, path,
         "[SYNC] Transition: took unsigned %s from %s at %s on the fleet key alone; the window "
         "closes %s, or for this machine as soon as it enrols", path, label, address,
         _iso(state.get("until")))
    return node.node_id
