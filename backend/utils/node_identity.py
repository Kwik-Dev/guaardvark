"""This machine's node id, from one place.

The id is a UUID kept in ~/.guaardvark/node_id, the file
backend/services/hardware_detector.py creates. start.sh exports it as
CLUSTER_NODE_ID; a backend started another way reads the file. Everything that
names this machine (the cluster proxy and routing, the client heartbeat,
broadcasts, autoresearch and self-improvement records) asks here, so a record
made on one machine carries that machine's id.
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path

NODE_ID_ENV = "CLUSTER_NODE_ID"
DEFAULT_NODE_ID_PATH = Path.home() / ".guaardvark" / "node_id"

_lock = threading.Lock()
# The id read from (or written to) the file, per path, so an unwritable home
# still gives one id for the life of the process.
_from_file: dict[str, str] = {}


def local_node_id() -> str:
    """This machine's id. The file is created the first time, as the hardware
    detector would; CLUSTER_NODE_ID wins when set."""
    from_env = (os.environ.get(NODE_ID_ENV) or "").strip()
    if from_env:
        return from_env
    path = Path(DEFAULT_NODE_ID_PATH)
    key = str(path)
    with _lock:
        cached = _from_file.get(key)
        if cached:
            return cached
        node_id = _read(path) or _create(path)
        _from_file[key] = node_id
        return node_id


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _create(path: Path) -> str:
    new_id = str(uuid.uuid4())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL: when two processes start together, both keep the first id.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return _read(path) or new_id
    except OSError:
        return new_id
    with os.fdopen(fd, "w") as handle:
        handle.write(new_id)
    return new_id
