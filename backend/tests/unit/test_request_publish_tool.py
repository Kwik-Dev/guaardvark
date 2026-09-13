"""request_publish queues for approval and never lets a caller pick its source."""

import pytest

from backend.tools import connection_tools as ct
from backend.utils.backend_http import BackendResponse

DISCORD = {"id": 3, "provider": "discord_webhook", "display_name": "Announcements",
           "handle": None, "enabled": True}
BLUESKY = {"id": 5, "provider": "bluesky", "display_name": "Project account",
           "handle": "@project", "enabled": True}
DISABLED = {"id": 9, "provider": "mastodon", "display_name": "Old", "handle": None,
            "enabled": False}


@pytest.fixture(scope="session", autouse=True)
def _isolate_gpu_lock_file():
    """No GPU access here."""
    yield


def _unexpected(*args, **kwargs):
    pytest.fail("unexpected call")


@pytest.fixture
def local(monkeypatch):
    """In-process surface: connections listed and queued without HTTP."""
    calls = []

    def queue(**kwargs):
        calls.append(kwargs)
        return {"queued": [{"publish_record_id": 41, "status": "awaiting_approval"}],
                "requires_approval": True, "count": 1}

    monkeypatch.setattr(ct, "request_json", _unexpected)
    monkeypatch.setattr(ct, "_list_local", lambda: [DISCORD, DISABLED])
    monkeypatch.setattr(ct, "_queue_local", queue)
    return calls


def _tool(transport=None):
    tool = ct.RequestPublishTool()
    if transport is not None:
        tool.set_context({"transport": transport})
    return tool


@pytest.mark.parametrize("transport, source", [("chat", "chat"), (None, "unknown"),
                                               ("ui", "unknown")])
def test_source_comes_from_the_surface(local, transport, source):
    result = _tool(transport).execute(body="v2.9.0 is out")

    assert result.success, result.error
    assert local[0]["requested_by"] == source
    assert local[0]["connection_ids"] == [3]
    assert result.output["status"] == "awaiting_approval"


def test_an_argument_cannot_claim_the_ui(local):
    result = _tool("chat").execute(body="hello", requested_by="ui")

    assert result.success
    assert local[0]["requested_by"] == "chat"


def test_mcp_goes_through_the_backend(monkeypatch):
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return BackendResponse(200, None, {"connections": [DISCORD, BLUESKY]})
        return BackendResponse(202, None, {
            "queued": [{"publish_record_id": 42, "status": "awaiting_approval"}],
            "requires_approval": True, "count": 1})

    monkeypatch.setattr(ct, "_list_local", _unexpected)
    monkeypatch.setattr(ct, "_queue_local", _unexpected)
    monkeypatch.setattr(ct, "request_json", fake_request)

    result = _tool("mcp").execute(body="hello", connection="@project")

    assert result.success, result.error
    assert calls[0][:2] == ("GET", "/api/connections")
    method, path, kwargs = calls[1]
    assert (method, path) == ("POST", "/api/connections/publish")
    assert kwargs["payload"]["requested_by"] == "mcp"
    assert kwargs["payload"]["connection_ids"] == [5]
    assert result.metadata == {"publish_record_ids": [42], "requires_approval": True}


def test_several_connections_need_a_choice(monkeypatch, local):
    monkeypatch.setattr(ct, "_list_local", lambda: [DISCORD, BLUESKY])

    result = _tool("chat").execute(body="hello")

    assert not result.success
    assert "Announcements (discord_webhook, id 3)" in result.error
    assert local == []


@pytest.mark.parametrize("connections", [[], [DISABLED]])
def test_no_usable_connection_queues_nothing(monkeypatch, local, connections):
    monkeypatch.setattr(ct, "_list_local", lambda: connections)

    result = _tool("chat").execute(body="hello")

    assert not result.success
    assert "Connections page" in result.error
    assert local == []


def test_a_disabled_connection_cannot_be_named(local):
    result = _tool("chat").execute(body="hello", connection="9")

    assert not result.success
    assert local == []


def test_empty_body_is_refused(local):
    result = _tool("chat").execute(body="   ")

    assert not result.success
    assert local == []
