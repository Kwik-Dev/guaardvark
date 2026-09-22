"""Per-recipe usage and verdict counters, kept beside recipes.json, not in it.

recipes.json is a tracked stock file; these numbers are machine state that
changes on every run, so they live in a gitignored sidecar with the same
recipe names as keys:

    {"youtube_search": {"ups": 0, "downs": 1, "runs": 3, "fallbacks": 1,
                        "last_used": "...", "disabled": true,
                        "disabled_reason": "thumbs_down:42", "provisional": false}}

Feedback disables a recipe (operator decision 2026-09-22: one thumbs-down on
a run that executed it), the matcher skips disabled recipes, and an un-thumb
re-enables. Auto-induced recipes start provisional and graduate on clean
thumbs-up. Nothing here touches a recipe body.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_DEFAULT = {"ups": 0, "downs": 0, "runs": 0, "fallbacks": 0, "last_used": None,
            "disabled": False, "disabled_reason": None, "provisional": False}


def path() -> str:
    from backend.config import GUAARDVARK_ROOT
    return os.path.join(GUAARDVARK_ROOT, "data", "agent", "recipe_stats.json")


def load() -> Dict[str, Dict[str, Any]]:
    try:
        with open(path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"recipe_stats unreadable, starting empty: {e}")
        return {}


def get(name: str) -> Dict[str, Any]:
    return {**_DEFAULT, **(load().get(name) or {})}


def is_disabled(name: str) -> bool:
    return bool((load().get(name) or {}).get("disabled"))


def _write(data: Dict[str, Dict[str, Any]]) -> None:
    p = path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, p)
    _bust_recipe_cache()


def _bust_recipe_cache() -> None:
    """The matcher reads recipes through a class cache keyed on recipes.json
    mtime; the sidecar is read live, so nothing to bust there. Kept as one
    place to extend if a cache is ever added."""
    return None


def _update(name: str, **changes) -> Dict[str, Any]:
    with _LOCK:
        data = load()
        entry = {**_DEFAULT, **(data.get(name) or {})}
        entry.update(changes)
        data[name] = entry
        _write(data)
        return entry


def bump(name: str, **deltas: int) -> Dict[str, Any]:
    """Add to counters: bump("x", ups=1) or bump("x", downs=-1)."""
    with _LOCK:
        data = load()
        entry = {**_DEFAULT, **(data.get(name) or {})}
        for k, d in deltas.items():
            entry[k] = max(0, int(entry.get(k) or 0) + int(d))
        data[name] = entry
        _write(data)
        return entry


def touch_run(name: str, fallback: bool = False) -> Dict[str, Any]:
    with _LOCK:
        data = load()
        entry = {**_DEFAULT, **(data.get(name) or {})}
        entry["runs"] = int(entry.get("runs") or 0) + 1
        if fallback:
            entry["fallbacks"] = int(entry.get("fallbacks") or 0) + 1
        entry["last_used"] = datetime.now().isoformat()
        data[name] = entry
        _write(data)
        return entry


def set_disabled(name: str, flag: bool, reason: Optional[str] = None) -> Dict[str, Any]:
    return _update(name, disabled=bool(flag), disabled_reason=(reason if flag else None))


def set_provisional(name: str, flag: bool) -> Dict[str, Any]:
    return _update(name, provisional=bool(flag))
