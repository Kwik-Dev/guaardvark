"""Which mode each declared decision runs in: off, shadow or on.

    off     today's code decides and no model is asked; every decision ships off
    shadow  today's code still decides; the model's answer is only recorded
    on      decide() uses the model's answer where the loaded model qualifies

A decision's mode is one settings row, ``decision_mode.<decision id>``. A
missing or unreadable row is the decision's ``mode_default`` in decision_data,
which is off for every decision. GUAARDVARK_DECISION_MODES, when set, wins
over the rows for the processes that see it: JSON
(``{"needs_live_data": "shadow"}``) or pairs (``needs_live_data=shadow,chat_route=on``).

decide() reads the mode from request threads, worker threads with no Flask app
context, and Celery workers, so each value is kept for CACHE_TTL_S per process;
a change made in one process reaches the others within that time.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

from backend.services.decision_data import DECISIONS

logger = logging.getLogger(__name__)

MODES = ("off", "shadow", "on")
SETTING_PREFIX = "decision_mode."
# settings_utils.ENV_VAR_MAP key naming the override variable.
ENV_KEY = "decision_modes"
CACHE_TTL_S = 30.0

_cache: dict = {}
_lock = threading.Lock()
_env_seen: dict = {"raw": None, "modes": {}}


def _env_name() -> str:
    from backend.utils.settings_utils import ENV_VAR_MAP

    return ENV_VAR_MAP[ENV_KEY]


def _parse_overrides(raw: str) -> dict:
    """{decision id: mode} from the override variable; bad entries are dropped."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    pairs: list = []
    if raw.startswith("{"):
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("%s is not valid JSON; ignored", _env_name())
            return {}
        if isinstance(loaded, dict):
            pairs = list(loaded.items())
    else:
        for part in raw.split(","):
            if "=" in part:
                key, _, value = part.partition("=")
                pairs.append((key, value))
            elif part.strip():
                pairs.append((part, None))
    modes = {}
    for key, value in pairs:
        decision_id = str(key).strip()
        mode = str(value).strip().lower() if isinstance(value, str) else None
        if mode in MODES and decision_id:
            modes[decision_id] = mode
        else:
            logger.warning("%s: ignoring %r=%r (mode must be one of %s)", _env_name(), key, value, MODES)
    return modes


def _env_overrides() -> dict:
    raw = os.environ.get(_env_name())
    with _lock:
        if raw != _env_seen["raw"]:
            _env_seen.update(raw=raw, modes=_parse_overrides(raw or ""))
        return dict(_env_seen["modes"])


def _read_row(key: str) -> Optional[str]:
    """One settings row's value, from a request or from a thread with no app context."""
    try:
        from flask import has_app_context

        if has_app_context():
            from backend.utils.settings_utils import get_setting

            return get_setting(key, default=None)
    except Exception as e:  # noqa: BLE001 - an unreadable mode reads as the default
        logger.warning("decision mode %s: settings read failed: %s", key, e)
        return None
    from backend.celery_beat_gates import _read_setting

    return _read_setting(key, None)


def default_mode(decision_id: str) -> str:
    mode = (DECISIONS.get(decision_id) or {}).get("mode_default", "off")
    return mode if mode in MODES else "off"


def get_decision_mode(decision_id: str) -> str:
    """off, shadow or on for ``decision_id``. Never raises; anything unreadable is the default."""
    override = _env_overrides().get(decision_id)
    if override:
        return override
    now = time.monotonic()
    with _lock:
        hit = _cache.get(decision_id)
    if hit is not None and now - hit[1] < CACHE_TTL_S:
        return hit[0]
    try:
        value = _read_row(SETTING_PREFIX + decision_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("decision mode %s: %s", decision_id, e)
        value = None
    mode = value.strip().lower() if isinstance(value, str) else None
    if mode not in MODES:
        mode = default_mode(decision_id)
    with _lock:
        _cache[decision_id] = (mode, now)
    return mode


def set_decision_mode(decision_id: str, mode: str) -> str:
    """Store ``mode`` for ``decision_id`` and return it.

    Needs a Flask app context (a route, ``flask shell``). Raises ValueError for
    an undeclared decision or a mode outside MODES, RuntimeError when the row
    could not be written. The override variable, when it names this
    decision, still wins in the processes that see it.
    """
    if decision_id not in DECISIONS:
        raise ValueError(f"unknown decision {decision_id!r}")
    mode = str(mode or "").strip().lower()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
    from flask import has_app_context

    if not has_app_context():
        raise RuntimeError("set_decision_mode needs a Flask app context")
    from backend.models import Setting, db
    from backend.utils.settings_utils import save_setting

    key = SETTING_PREFIX + decision_id
    save_setting(key, mode)
    row = db.session.get(Setting, key)
    if row is None or row.value != mode:
        raise RuntimeError(f"could not store {key}")
    with _lock:
        _cache[decision_id] = (mode, time.monotonic())
    if decision_id in _env_overrides():
        logger.warning("%s sets %s here; the stored mode applies where it is unset", _env_name(), decision_id)
    return mode


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _env_seen.update(raw=None, modes={})
