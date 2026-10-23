"""One source for this machine's node id (backend/utils/node_identity.py).

CLUSTER_NODE_ID wins when start.sh exported it; otherwise the id comes from
~/.guaardvark/node_id, created on first use. No other module reads either
environment variable, so a learning, a self-improvement run or a broadcast
made on one machine names that machine rather than a placeholder.
"""
import logging
import os
import re
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.utils import node_identity

BACKEND = Path(__file__).resolve().parents[1]
ENV_READ = re.compile(
    r"""(?:environ(?:\.get)?\s*\(|getenv\s*\(|environ\s*\[)\s*["'](?:CLUSTER_NODE_ID|GUAARDVARK_NODE_ID)["']"""
)
# config.py mirrors the environment into app.config; nothing reads it back.
ALLOWED = {BACKEND / "utils" / "node_identity.py", BACKEND / "config.py"}


@pytest.fixture
def id_file(tmp_path, monkeypatch):
    path = tmp_path / "guaardvark" / "node_id"
    monkeypatch.setattr(node_identity, "DEFAULT_NODE_ID_PATH", path)
    monkeypatch.delenv(node_identity.NODE_ID_ENV, raising=False)
    return path


def test_the_environment_wins(id_file, monkeypatch):
    id_file.parent.mkdir(parents=True)
    id_file.write_text("from-file")
    monkeypatch.setenv(node_identity.NODE_ID_ENV, "from-env")
    assert node_identity.local_node_id() == "from-env"


def test_the_file_is_read_without_the_environment(id_file):
    id_file.parent.mkdir(parents=True)
    id_file.write_text("from-file\n")
    assert node_identity.local_node_id() == "from-file"


def test_a_missing_file_is_created_once(id_file):
    first = node_identity.local_node_id()
    assert str(uuid.UUID(first)) == first
    assert id_file.read_text() == first
    assert node_identity.local_node_id() == first


def test_an_unwritable_home_still_gives_one_id_per_process(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    monkeypatch.setattr(node_identity, "DEFAULT_NODE_ID_PATH", blocker / "node_id")
    monkeypatch.delenv(node_identity.NODE_ID_ENV, raising=False)
    first = node_identity.local_node_id()
    assert first and node_identity.local_node_id() == first


def _backend_sources():
    skip = {"tests", "venv", ".venv", "node_modules", "__pycache__"}
    for root, dirs, files in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in files:
            if name.endswith(".py"):
                yield Path(root) / name


def test_no_other_module_reads_the_node_id_from_the_environment():
    readers = [
        str(path.relative_to(BACKEND))
        for path in _backend_sources()
        if path not in ALLOWED and ENV_READ.search(path.read_text(errors="ignore"))
    ]
    assert readers == []


def test_the_routing_broadcast_names_this_machine(id_file, caplog):
    """The routing broadcast builds and logs with the id the heartbeat sends."""
    from backend.services import cluster_routing

    id_file.parent.mkdir(parents=True)
    id_file.write_text("machine-a")

    class _Table:
        fleet_hash = "h1"
        node_count = 1

        def to_dict(self):
            return {}

    class _Store:
        def get(self):
            return None

        def set(self, table, persist=True):
            pass

    with patch.dict("os.environ", {"CLUSTER_ROLE": "master"}), \
            patch.object(cluster_routing, "get_routing_store", return_value=_Store()), \
            patch.object(cluster_routing.RoutingTableBuilder, "build", return_value=_Table()) as build, \
            patch("backend.socketio_instance.socketio.emit"), \
            caplog.at_level(logging.INFO, logger=cluster_routing.__name__):
        cluster_routing.recompute_and_broadcast(reason="test")
    assert build.call_args.kwargs["master_node_id"] == "machine-a"
    assert "recomputed by machine-a" in caplog.text
