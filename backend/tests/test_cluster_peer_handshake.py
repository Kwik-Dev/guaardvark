"""Socket.IO handshakes between machines are signed (backend/socketio_events.py).

With the cluster on, a handshake signed by a machine holding a certificate from
the fleet's master is a peer; one carrying the old node-id api_key, or a bad
signature, is refused. Only a session the master opened with its own key may
replace a worker's routing table.
"""
from unittest.mock import MagicMock

import flask
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import backend.socketio_events as events
from backend.utils import node_auth

MASTER = Ed25519PrivateKey.generate()
MASTER_PUB = node_auth.public_key_text(MASTER)


@pytest.fixture
def app(monkeypatch):
    application = flask.Flask(__name__)
    application.config["CLUSTER_ENABLED"] = True
    monkeypatch.setattr(node_auth, "authority", lambda: (MASTER_PUB, "master-id"))
    monkeypatch.setattr(events, "join_room", MagicMock())
    events._peer_sessions.clear()
    yield application
    events._peer_sessions.clear()


def _handshake(key, node_id, cert, **override):
    headers = node_auth.sign_request(key, node_id, cert, node_auth.SOCKET_METHOD, node_auth.SOCKET_PATH)
    auth = {
        "gx_node": headers[node_auth.HEADER_NODE],
        "gx_cert": headers[node_auth.HEADER_CERT],
        "gx_ts": headers[node_auth.HEADER_TS],
        "gx_nonce": headers[node_auth.HEADER_NONCE],
        "gx_sig": headers[node_auth.HEADER_SIG],
    }
    auth.update(override)
    return auth


def _client_handshake(node_id="worker-b"):
    key = Ed25519PrivateKey.generate()
    cert = node_auth.issue_certificate(MASTER, node_id, node_auth.public_key_text(key), "master-id")
    return _handshake(key, node_id, cert)


def _master_handshake():
    cert = node_auth.issue_certificate(MASTER, "master-id", MASTER_PUB, "master-id")
    return _handshake(MASTER, "master-id", cert)


def _check(app, auth, sid="sid-1", cookies=None):
    environ = {"HTTP_COOKIE": cookies} if cookies else {}
    with app.test_request_context("/socket.io/", environ_base=environ):
        flask.request.sid = sid
        return events._cluster_auth_check(auth)


def test_solo_mode_accepts_every_handshake(app):
    app.config["CLUSTER_ENABLED"] = False
    assert _check(app, {"api_key": "anything"}) is True


def test_an_enrolled_machine_is_a_peer(app):
    assert _check(app, _client_handshake()) is True
    assert events._peer_sessions["sid-1"] == {"node": "worker-b", "master": False}
    events.join_room.assert_called_once_with("cluster:masters-broadcast")


def test_the_master_is_recognised_by_its_own_key(app):
    assert _check(app, _master_handshake(), sid="sid-m") is True
    assert events._peer_sessions["sid-m"]["master"] is True


def test_the_old_node_id_key_is_refused(app):
    assert _check(app, {"api_key": "worker-b"}) is False
    assert events._peer_sessions == {}


def test_a_bad_signature_is_refused(app):
    auth = _client_handshake()
    auth["gx_sig"] = _client_handshake()["gx_sig"]
    assert _check(app, auth) is False


def test_a_certificate_from_another_master_is_refused(app):
    key = Ed25519PrivateKey.generate()
    foreign = node_auth.issue_certificate(Ed25519PrivateKey.generate(), "worker-b",
                                          node_auth.public_key_text(key), "elsewhere")
    assert _check(app, _handshake(key, "worker-b", foreign)) is False


def test_a_replayed_handshake_is_refused(app):
    auth = _client_handshake()
    assert _check(app, auth) is True
    assert _check(app, auth, sid="sid-2") is False


def test_a_browser_with_its_cookies_still_connects(app):
    assert _check(app, None, cookies="theme=dark") is True
    assert _check(app, None) is False  # no cookies and no signature


def test_only_the_masters_session_may_replace_the_routing_table(app, monkeypatch):
    from backend.services.cluster_routing import get_routing_store

    monkeypatch.delenv("CLUSTER_MASTER_NODE_ID", raising=False)
    store = get_routing_store()
    monkeypatch.setattr(store, "set", MagicMock())
    table = {"computed_by": "master-id", "routes": {}, "computed_at": "2026-10-06T00:00:00",
             "node_count": 1, "fleet_hash": "h"}
    _check(app, _client_handshake(), sid="sid-worker")
    _check(app, _master_handshake(), sid="sid-master")
    for sid in ("sid-worker", "sid-unknown"):
        with app.test_request_context("/socket.io/"):
            flask.request.sid = sid
            events.handle_cluster_routing_table(table)
    store.set.assert_not_called()
    with app.test_request_context("/socket.io/"):
        flask.request.sid = "sid-master"
        events.handle_cluster_routing_table(table)
    store.set.assert_called_once()
