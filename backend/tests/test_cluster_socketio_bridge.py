from unittest.mock import MagicMock, patch
import pytest

# Stands in for this machine's signed handshake (backend/utils/node_auth.py).
_HANDSHAKE = {"gx_node": "n1", "gx_cert": "c", "gx_ts": "1", "gx_nonce": "n" * 16, "gx_sig": "s"}


@pytest.fixture(autouse=True)
def _signed_handshake(monkeypatch):
    import backend.services.cluster_socketio_bridge as bridge_mod
    monkeypatch.setattr(bridge_mod, "socket_auth", lambda: dict(_HANDSHAKE))


def test_bridge_open_connects_with_a_signed_handshake():
    from backend.services.cluster_socketio_bridge import SocketIOChatBridge
    from backend.services.cluster_proxy import NodeTarget
    target = NodeTarget("n2", "192.168.1.20", 5002)
    with patch("socketio.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        bridge = SocketIOChatBridge("sess-1", target)
        bridge.open()
        mock_client.connect.assert_called_once()
        call_kwargs = mock_client.connect.call_args.kwargs
        assert call_kwargs["auth"] == _HANDSHAKE
        # No node id sent as a key, in a header or the auth payload.
        assert "X-Guaardvark-API-Key" not in (call_kwargs.get("headers") or {})
        assert "api_key" not in call_kwargs["auth"]


def test_bridge_forward_send_emits_chat_send():
    from backend.services.cluster_socketio_bridge import SocketIOChatBridge
    from backend.services.cluster_proxy import NodeTarget
    bridge = SocketIOChatBridge("sess-1", NodeTarget("n2", "h", 5002))
    bridge._client = MagicMock()
    bridge.forward_send({"message": "hi"})
    bridge._client.emit.assert_called_once_with("chat:send", {"message": "hi"})


def test_bridge_forward_send_opens_lazily():
    """If forward_send is called before open, open runs automatically."""
    from backend.services.cluster_socketio_bridge import SocketIOChatBridge
    from backend.services.cluster_proxy import NodeTarget
    with patch("socketio.Client") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        bridge = SocketIOChatBridge("sess-1", NodeTarget("n2", "h", 5002))
        bridge.forward_send({"message": "hi"})
        # open should have been called (via forward_send's lazy open)
        mock_client.connect.assert_called_once()
        mock_client.emit.assert_called_once_with("chat:send", {"message": "hi"})


def test_registry_reuses_bridge_per_session():
    from backend.services.cluster_socketio_bridge import SocketIOBridgeRegistry
    from backend.services.cluster_proxy import NodeTarget
    SocketIOBridgeRegistry._bridges.clear()
    target = NodeTarget("n2", "h", 5002)
    with patch("backend.services.cluster_socketio_bridge.SocketIOChatBridge"):
        b1 = SocketIOBridgeRegistry.get_or_create("sess-regA", target)
        b2 = SocketIOBridgeRegistry.get_or_create("sess-regA", target)
    assert b1 is b2
    SocketIOBridgeRegistry._bridges.clear()


def test_registry_close_for_session():
    from backend.services.cluster_socketio_bridge import SocketIOBridgeRegistry
    SocketIOBridgeRegistry._bridges.clear()
    mock_bridge = MagicMock()
    SocketIOBridgeRegistry._bridges["sess-close-x"] = mock_bridge
    SocketIOBridgeRegistry.close_for_session("sess-close-x")
    mock_bridge.close.assert_called_once()
    assert "sess-close-x" not in SocketIOBridgeRegistry._bridges


def test_registry_close_nonexistent_session_is_safe():
    from backend.services.cluster_socketio_bridge import SocketIOBridgeRegistry
    # Should not raise
    SocketIOBridgeRegistry.close_for_session("nonexistent-sid")
