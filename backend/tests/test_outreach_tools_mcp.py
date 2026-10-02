"""Outreach tools on the MCP path: arguments are checked before anything runs,
and the status comes from the backend or is reported as unknown.

No database, network or model: the backend HTTP client, the persona and the
settings lookup are replaced with stand-ins.
"""

from __future__ import annotations

import pytest

from backend.tests._mcp_sdk import use_mcp_sdk

use_mcp_sdk()  # the adapter imports mcp.types; see backend/tests/_mcp_sdk.py

from backend.mcp.tools_adapter import _tool_input_schema  # noqa: E402
from backend.services.social_outreach import kill_switch, transitions  # noqa: E402
from backend.tools import outreach_tools as ot  # noqa: E402
from backend.utils.backend_http import BackendError, BackendResponse  # noqa: E402


def _mcp(tool):
    tool.set_context({"transport": "mcp"})
    return tool


def _row(row_id, status, created):
    return {"id": row_id, "platform": "reddit", "action": "comment", "status": status,
            "grade_score": 0.8, "target_url": "u", "draft_text": f"d{row_id}", "created_at": created}


class _Backend:
    """Answers request_json from a path -> rows table and records every call."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, method, path, payload=None, **kwargs):
        self.calls.append((method, path, payload))
        if method == "POST":
            data = {"id": 1, "status": "drafted"}
            return BackendResponse(status=201, body=data, data=data)
        rows = self.answers.get(path, [])
        return BackendResponse(status=200, body=rows, data=rows)


@pytest.fixture
def backend(monkeypatch):
    fake = _Backend({
        # GET /approved answers oldest first, the order the posting tick takes.
        "/api/social-outreach/approved": [
            _row(1, "approved", "2026-09-01T10:00:00"),
            _row(2, "approved", "2026-09-02T10:00:00"),
            _row(3, "approved", "2026-09-03T10:00:00"),
        ],
        "/api/social-outreach/queue": [
            _row(6, "drafted", "2026-09-06T10:00:00"),
            _row(5, "drafted", "2026-09-05T10:00:00"),
        ],
        "/api/social-outreach/audit": [
            _row(9, "posted", "2026-09-09T10:00:00"),
            _row(8, "rejected", "2026-09-08T10:00:00"),
            _row(7, "posted", "2026-09-07T10:00:00"),
        ],
    })
    monkeypatch.setattr(ot, "request_json", fake)
    return fake


@pytest.fixture
def persona_modes(monkeypatch):
    modes = []

    def draft(**kwargs):
        modes.append(kwargs["mode"])
        return {"draft": "a draft", "grade": 0.9, "reason": "r"}

    monkeypatch.setattr(ot.persona, "draft_outreach_text", draft)
    return modes


# ---- outreach_draft_post: mode ---------------------------------------------------

@pytest.mark.parametrize("mode", ["comments", "reply", "post", "shares"])
def test_draft_refuses_a_mode_it_does_not_have(backend, persona_modes, mode):
    result = _mcp(ot.OutreachDraftPostTool()).execute(
        platform="reddit", mode=mode, thread_context="a thread about local models")

    assert not result.success
    assert "mode must be one of" in result.error
    assert persona_modes == []   # nothing was drafted
    assert backend.calls == []   # nothing was queued


@pytest.mark.parametrize("mode", ["comment", "Comment", " comment ", "", None])
def test_draft_comment_mode_queues_a_comment(backend, persona_modes, mode):
    result = _mcp(ot.OutreachDraftPostTool()).execute(
        platform="reddit", mode=mode, thread_context="a thread about local models")

    assert result.success, result.error
    assert persona_modes == ["comment"]
    assert [payload["action"] for _, _, payload in backend.calls] == ["comment"]


def test_draft_mode_is_published_as_an_enum():
    schema = _tool_input_schema(ot.OutreachDraftPostTool())

    assert schema["properties"]["mode"]["enum"] == ["comment", "share"]


# ---- outreach_list_queue: status and order -----------------------------------------

@pytest.mark.parametrize("status", ["pending", "queued", "done"])
def test_list_refuses_a_status_that_does_not_exist(backend, status):
    result = _mcp(ot.OutreachListQueueTool()).execute(status=status)

    assert not result.success
    assert "status must be one of" in result.error
    assert backend.calls == []


def test_list_refuses_an_unknown_status_in_the_backend_too():
    # The chat path would otherwise run a query that can only match nothing.
    result = ot.OutreachListQueueTool().execute(status="pending")

    assert not result.success
    assert "status must be one of" in result.error


def test_list_approved_is_oldest_first_like_the_posting_tick(backend):
    result = _mcp(ot.OutreachListQueueTool()).execute(status="approved", limit=2)

    assert result.success
    assert [row["id"] for row in result.output["rows"]] == [1, 2]
    assert result.output["order"] == "oldest_first"


@pytest.mark.parametrize("status, ids", [("drafted", [6, 5]), ("posted", [9, 7]), ("rejected", [8])])
def test_list_other_statuses_are_newest_first(backend, status, ids):
    result = _mcp(ot.OutreachListQueueTool()).execute(status=status)

    assert [row["id"] for row in result.output["rows"]] == ids
    assert result.output["order"] == "newest_first"


def test_list_status_is_published_as_the_real_statuses():
    schema = _tool_input_schema(ot.OutreachListQueueTool())

    assert schema["properties"]["status"]["enum"] == list(transitions.KNOWN_STATUSES)
    assert schema["properties"]["status"]["default"] == "drafted"


# ---- outreach_status ----------------------------------------------------------------

_STATUS = {
    "enabled": True,
    "supervised": True,
    "settings_readable": True,
    "caps": {"min_gap_seconds": 1800, "daily_cap": 8, "servo_failure_abort_threshold": 2},
    "cadence": {"reddit": {"posts_in_24h": 1}},
}


def _status_backend(monkeypatch, answer):
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path))
        if isinstance(answer, BackendError):
            raise answer
        return BackendResponse(status=200, body=answer, data=answer)

    def local(*args, **kwargs):
        raise AssertionError("the MCP process must not read the database or Redis itself")

    monkeypatch.setattr(ot, "request_json", request)
    for name in ("is_enabled", "is_supervised", "cadence_status", "status_snapshot"):
        monkeypatch.setattr(ot.kill_switch, name, local)
    return calls


def test_status_over_mcp_is_the_backends_answer(monkeypatch):
    calls = _status_backend(monkeypatch, _STATUS)

    result = _mcp(ot.OutreachStatusTool()).execute()

    assert result.success, result.error
    assert result.output == _STATUS
    assert calls == [("GET", "/api/social-outreach/status")]


def test_status_over_mcp_says_the_backend_is_not_answering(monkeypatch):
    down = BackendError("unreachable", "The Guaardvark backend is not answering at http://127.0.0.1:5000.")
    _status_backend(monkeypatch, down)

    result = _mcp(ot.OutreachStatusTool()).execute()

    assert not result.success
    assert "not answering" in result.error
    assert result.output is None  # no "disabled, unsupervised" made up from defaults
    assert result.metadata["backend_error"] == "unreachable"


def test_status_with_unreadable_settings_is_unknown_not_off(monkeypatch):
    _status_backend(monkeypatch, {**_STATUS, "enabled": False, "supervised": True, "settings_readable": False})

    result = _mcp(ot.OutreachStatusTool()).execute()

    assert not result.success
    assert "unknown" in result.error
    assert result.output is None


def test_status_from_a_backend_without_the_readable_flag_is_accepted(monkeypatch):
    older = {key: value for key, value in _STATUS.items() if key != "settings_readable"}
    _status_backend(monkeypatch, older)

    result = _mcp(ot.OutreachStatusTool()).execute()

    assert result.success
    assert result.output == older


# ---- what the backend reports (kill_switch) ---------------------------------------------

def test_snapshot_reads_the_stored_settings(monkeypatch):
    stored = {"social_outreach_enabled": "true"}  # supervised never set
    monkeypatch.setattr(kill_switch, "_lookup_setting", stored.get)
    monkeypatch.setattr(kill_switch, "cadence_status", lambda: {})

    snapshot = kill_switch.status_snapshot()

    assert (snapshot["enabled"], snapshot["supervised"], snapshot["settings_readable"]) == (True, False, True)
    assert kill_switch.is_enabled() is True
    assert kill_switch.is_supervised() is False


def test_unreadable_settings_fail_closed_and_are_flagged(monkeypatch):
    def unreadable(key):
        raise RuntimeError("database is down")

    monkeypatch.setattr(kill_switch, "_lookup_setting", unreadable)
    monkeypatch.setattr(kill_switch, "cadence_status", lambda: {})

    snapshot = kill_switch.status_snapshot()

    assert snapshot["settings_readable"] is False
    # The posting paths see "off" and "supervised": nothing posts on a guess.
    assert kill_switch.is_enabled() is False
    assert kill_switch.is_supervised() is True
    assert (snapshot["enabled"], snapshot["supervised"]) == (False, True)
