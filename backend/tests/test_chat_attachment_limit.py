"""The chat attachment limit is declared once and enforced on both entry points.

A ``chat:send`` packet and a ``POST /api/chat/unified`` body carry the same
base64 ``image``; both must answer an oversized one instead of dropping it,
and the Socket.IO buffer must be wide enough that the check is reachable.
"""

import base64
from unittest.mock import patch

import pytest
from flask import Flask

from backend.socketio_instance import (
    CHAT_ATTACHMENT_MAX_BYTES,
    SOCKET_MAX_HTTP_BUFFER_SIZE,
    chat_attachment_size_bytes,
    chat_attachment_too_large,
    socketio,
)


def _b64_of(size: int) -> str:
    return base64.b64encode(b"\xff" * size).decode("ascii")


# ---- the helper -------------------------------------------------------------

@pytest.mark.parametrize("size", [0, 1, 2, 3, 4, 1000, 36 * 1024])
def test_size_is_computed_exactly_without_decoding(size):
    assert chat_attachment_size_bytes(_b64_of(size)) == size


def test_size_accepts_data_url_and_wrapped_lines():
    raw = _b64_of(3000)
    assert chat_attachment_size_bytes("data:image/png;base64," + raw) == 3000
    wrapped = "\n".join(raw[i:i + 76] for i in range(0, len(raw), 76))
    assert chat_attachment_size_bytes(wrapped) == 3000


@pytest.mark.parametrize("value", [None, "", 42, {"image": "x"}, []])
def test_non_attachments_count_as_zero(value):
    assert chat_attachment_size_bytes(value) == 0
    assert chat_attachment_too_large(value) is None


def test_limit_is_inclusive_and_the_message_names_both_sizes():
    assert chat_attachment_too_large(_b64_of(CHAT_ATTACHMENT_MAX_BYTES)) is None
    reason = chat_attachment_too_large(_b64_of(CHAT_ATTACHMENT_MAX_BYTES + 1))
    assert reason is not None
    assert "16.0 MB" in reason and "limit is 16 MB" in reason


# ---- the Socket.IO buffer ----------------------------------------------------

def test_socket_buffer_carries_the_largest_allowed_attachment_as_base64():
    """The server-side check only runs on packets engineio lets through."""
    assert socketio.server_options["max_http_buffer_size"] == SOCKET_MAX_HTTP_BUFFER_SIZE
    largest_packet = len(_b64_of(CHAT_ATTACHMENT_MAX_BYTES)) + 64 * 1024
    assert SOCKET_MAX_HTTP_BUFFER_SIZE >= largest_packet
    # The case from the report: a 1.3 MB photo has to reach the handler.
    assert SOCKET_MAX_HTTP_BUFFER_SIZE > len(_b64_of(int(1.3 * 1024 * 1024)))


# ---- the socket handler ------------------------------------------------------

def test_chat_send_answers_chat_error_for_oversized_image():
    from backend import socketio_events

    payload = {"session_id": "s-1", "message": "look", "image": _b64_of(CHAT_ATTACHMENT_MAX_BYTES + 1)}
    with patch.object(socketio_events, "emit") as mock_emit, \
         patch.object(socketio_events, "_handle_chat_send_local") as mock_local:
        socketio_events.handle_chat_send(payload)

    mock_local.assert_not_called()
    mock_emit.assert_called_once()
    event, data = mock_emit.call_args.args
    assert event == "chat:error"
    assert data["code"] == "attachment_too_large"
    assert data["session_id"] == "s-1"
    assert "16 MB" in data["error"]


def test_chat_send_passes_a_fitting_image_through():
    from backend import socketio_events

    payload = {"session_id": "s-2", "message": "look", "image": _b64_of(36 * 1024)}
    with patch.object(socketio_events, "emit") as mock_emit, \
         patch.object(socketio_events, "_handle_chat_send_local") as mock_local:
        # No app context here: the cluster-routing block fails and the handler
        # falls through to local handling, which is the path under test.
        socketio_events.handle_chat_send(payload)

    mock_local.assert_called_once_with(payload)
    mock_emit.assert_not_called()


# ---- the HTTP route and the config route -----------------------------------

@pytest.fixture
def client():
    from backend.api.unified_chat_api import chat_config_bp, unified_chat_bp

    app = Flask("test_chat_attachment_limit")
    app.register_blueprint(unified_chat_bp)
    app.register_blueprint(chat_config_bp)
    return app.test_client()


def test_http_route_returns_413_for_oversized_image(client):
    res = client.post("/api/chat/unified", json={
        "session_id": "s-3", "message": "look", "image": _b64_of(CHAT_ATTACHMENT_MAX_BYTES + 1),
    })
    assert res.status_code == 413
    body = res.get_json()
    assert body["success"] is False
    assert body["code"] == "attachment_too_large"
    assert body["attachment_max_bytes"] == CHAT_ATTACHMENT_MAX_BYTES


def test_config_route_exposes_the_declared_limit(client):
    res = client.get("/api/chat/config")
    assert res.status_code == 200
    assert res.get_json()["data"]["attachment_max_bytes"] == CHAT_ATTACHMENT_MAX_BYTES == 16 * 1024 * 1024
