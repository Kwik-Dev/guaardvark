"""map_codebase never blocks past its wait budget: a fresh map is served, an
older one is served at once while a single background computation replaces it,
and a first map that takes too long answers "computing" until it is ready.

No network or GPU: the cache and the computation are replaced with stand-ins.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from backend.tools import workstation_tools as wt


def _mcp(tool):
    tool.set_context({"transport": "mcp"})
    return tool


# ---- map_codebase ------------------------------------------------------------------

SNAPSHOT = {
    "file_count": 3,
    "languages": ["python"],
    "stats": {},
    "findings": [{"id": "f1", "kind": "unwired-tool", "severity": "medium",
                  "summary": "tool X is registered but unused", "paths": ["backend/tools/x.py"]}],
}


def _computes_started(state, n=1, timeout=5.0):
    """Wait until the background thread has entered the computation n times."""
    deadline = time.monotonic() + timeout
    while state["computes"] < n and time.monotonic() < deadline:
        time.sleep(0.01)
    return state["computes"]


@pytest.fixture
def mapper(monkeypatch, tmp_path):
    """A controllable stand-in for the cache and the computation."""
    from backend.api import system_map_api

    state = {"cached": None, "age": None, "computes": 0, "gate": threading.Event(), "fail": None}
    state["gate"].set()

    def read_cached(root):
        if state["cached"] is None:
            return None, None
        return dict(state["cached"]), state["age"]

    def compute_and_cache(root):
        state["computes"] += 1
        state["gate"].wait(10)
        if state["fail"]:
            raise RuntimeError(state["fail"])
        payload = dict(SNAPSHOT, _cache={"hit": False, "computed_in_seconds": 0.0})
        state["cached"], state["age"] = dict(SNAPSHOT), 0.0
        return payload

    monkeypatch.setattr(system_map_api, "read_cached", read_cached)
    monkeypatch.setattr(system_map_api, "compute_and_cache", compute_and_cache)
    monkeypatch.setattr(wt, "_map_jobs", {})
    monkeypatch.setattr(wt, "_safe_root", lambda _arg: tmp_path)
    monkeypatch.setattr("backend.services.system_mapper.actions.ranked_findings",
                        lambda snapshot, root: list(snapshot.get("findings") or []))
    yield state
    state["gate"].set()
    for job in list(wt._map_jobs.values()):
        job.done.wait(5)


def test_fresh_map_is_served_without_computing(mapper):
    mapper["cached"], mapper["age"] = dict(SNAPSHOT), 30.0
    result = wt.MapCodebaseTool().execute()
    assert result.success
    assert result.output["cache"]["age_seconds"] == 30
    assert result.output["note"] is None
    assert mapper["computes"] == 0


def test_stale_map_is_served_at_once_and_refreshed_in_the_background(mapper, monkeypatch):
    mapper["cached"], mapper["age"] = dict(SNAPSHOT), 3 * 86400.0
    mapper["gate"].clear()
    monkeypatch.setattr(wt, "_MAP_WAIT_SECONDS", 30.0)

    t0 = time.monotonic()
    result = wt.MapCodebaseTool().execute()
    assert time.monotonic() - t0 < 5
    assert result.success
    assert result.output["file_count"] == 3
    assert result.output["cache"]["stale"] is True
    assert result.output["cache"]["refreshing"] is True
    assert "3 days old" in result.output["note"]

    # A second call while the refresh runs joins it instead of starting another.
    assert _computes_started(mapper) == 1
    wt.MapCodebaseTool().execute()
    assert mapper["computes"] == 1
    assert len(wt._map_jobs) == 1


def test_first_map_that_outlasts_the_wait_answers_computing(mapper, monkeypatch):
    mapper["gate"].clear()
    monkeypatch.setattr(wt, "_MAP_WAIT_SECONDS", 0.2)

    t0 = time.monotonic()
    result = _mcp(wt.MapCodebaseTool()).execute()
    assert time.monotonic() - t0 < 5
    assert result.success
    assert result.output["status"] == "computing"
    assert "call map_codebase again" in result.output["note"].lower()

    assert _computes_started(mapper) == 1
    again = _mcp(wt.MapCodebaseTool()).execute()
    assert again.output["status"] == "computing"
    assert mapper["computes"] == 1

    mapper["gate"].set()
    wt._map_jobs[str(Path(result.output["root"]))].done.wait(5)
    done = _mcp(wt.MapCodebaseTool()).execute()
    assert done.output["file_count"] == 3
    assert done.output["findings"][0]["id"] == "f1"
    assert mapper["computes"] == 1


def test_first_map_that_finishes_within_the_wait_is_returned(mapper):
    result = wt.MapCodebaseTool().execute()
    assert result.success
    assert result.output["file_count"] == 3
    assert result.output["cache"]["hit"] is False


def test_refresh_waits_for_the_new_map(mapper):
    mapper["cached"], mapper["age"] = dict(SNAPSHOT, file_count=1), 10.0
    result = wt.MapCodebaseTool().execute(refresh=True)
    assert result.output["file_count"] == 3
    assert mapper["computes"] == 1


def test_failed_refresh_keeps_the_last_map_and_says_why(mapper):
    mapper["cached"], mapper["age"] = dict(SNAPSHOT), 600.0
    mapper["fail"] = "disk full"
    result = wt.MapCodebaseTool().execute(refresh=True)
    assert result.success
    assert result.output["cache"]["refresh_error"] == "disk full"
    assert "disk full" in result.output["note"]


def test_failed_first_map_is_an_error(mapper):
    mapper["fail"] = "boom"
    result = wt.MapCodebaseTool().execute()
    assert not result.success
    assert "boom" in result.error


def test_wait_budget_stays_under_the_mcp_timeout(monkeypatch):
    from backend.mcp import config as mcp_config

    monkeypatch.setattr(mcp_config, "load_config", lambda: mcp_config.MCPConfig(timeout_seconds=30))
    assert wt._map_wait_seconds(True) == 15
    monkeypatch.setattr(mcp_config, "load_config", lambda: mcp_config.MCPConfig())
    assert wt._map_wait_seconds(True) == wt._MAP_WAIT_SECONDS < mcp_config.MCPConfig().timeout_seconds
