"""What a client takes from its master only when the master signed it.

A broadcast from the master starts /api/interconnector/sync/manual on each
client. That route answers only this machine or the API key (auth_guard), plus
a request signed with the master's own key, the one pinned at enrolment
(backend/utils/node_auth.py). Another enrolled machine's signature is not the
master's.

/api/interconnector/receive-directive reaches the code-manipulation kill
switches; it takes only the master's signature, whatever else the request
carries, and logs every refusal.
"""
import json
import logging

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask

from backend.utils import auth_guard, node_auth

MASTER = Ed25519PrivateKey.generate()
MASTER_PUB = node_auth.public_key_text(MASTER)
MASTER_CERT = node_auth.issue_certificate(MASTER, "master-id", MASTER_PUB, "master-id")
REMOTE = {"REMOTE_ADDR": "192.0.2.20"}
SYNC = "/api/interconnector/sync/manual"


def _client_signer():
    key = Ed25519PrivateKey.generate()
    cert = node_auth.issue_certificate(MASTER, "client-b", node_auth.public_key_text(key), "master-id")
    return key, "client-b", cert


@pytest.fixture
def client_app(monkeypatch):
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv(auth_guard.API_KEY_ENV, raising=False)
    monkeypatch.setattr(node_auth, "authority", lambda: (MASTER_PUB, "master-id"))
    app = Flask(__name__)
    app.before_request(auth_guard.check_endpoint_auth)
    ran = []

    @app.route(SYNC, methods=["POST"])
    def manual_sync():
        ran.append(SYNC)
        return {"ok": True}

    @app.route("/api/interconnector/config", methods=["POST"])
    def config():
        ran.append("config")
        return {"ok": True}

    app.ran = ran
    return app


def _post(app, path, body, signer=None, sign_body=None):
    headers = {"Content-Type": "application/json"}
    if signer:
        key, node_id, cert = signer
        headers.update(node_auth.sign_request(key, node_id, cert, "POST", path,
                                              sign_body if sign_body is not None else body))
    return app.test_client().post(path, data=body, headers=headers, environ_base=REMOTE)


BODY = json.dumps({"direction": "pull", "sync_files": True}).encode()


def test_an_unsigned_sync_request_from_another_machine_is_refused(client_app):
    assert _post(client_app, SYNC, BODY).status_code == 403
    assert client_app.ran == []


def test_the_master_may_start_a_sync(client_app):
    assert _post(client_app, SYNC, BODY, (MASTER, "master-id", MASTER_CERT)).status_code == 200
    assert client_app.ran == [SYNC]


def test_another_enrolled_machine_may_not(client_app):
    assert _post(client_app, SYNC, BODY, _client_signer()).status_code == 403
    assert client_app.ran == []


def test_a_masters_signature_for_another_body_is_refused(client_app):
    response = _post(client_app, SYNC, BODY, (MASTER, "master-id", MASTER_CERT),
                     sign_body=b'{"direction": "pull"}')
    assert response.status_code == 403


def test_a_masters_signature_opens_only_the_sync_route(client_app):
    response = _post(client_app, "/api/interconnector/config", BODY, (MASTER, "master-id", MASTER_CERT))
    assert response.status_code == 403
    assert client_app.ran == []


def test_a_replayed_master_request_is_refused(client_app):
    headers = {"Content-Type": "application/json",
               **node_auth.sign_request(MASTER, "master-id", MASTER_CERT, "POST", SYNC, BODY)}
    client = client_app.test_client()
    assert client.post(SYNC, data=BODY, headers=headers, environ_base=REMOTE).status_code == 200
    assert client.post(SYNC, data=BODY, headers=headers, environ_base=REMOTE).status_code == 403


# ---- directives ---------------------------------------------------------------

DIRECTIVE = "/api/interconnector/receive-directive"
DIRECTIVE_BODY = json.dumps({"directive": "lock_codebase", "reason": "test"}).encode()


@pytest.fixture
def directive_app(monkeypatch):
    from backend.api import interconnector_api as ic_api
    from backend.socketio_instance import socketio
    import backend.tools.agent_tools.code_manipulation_tools as cmt

    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv(auth_guard.API_KEY_ENV, raising=False)
    monkeypatch.setattr(node_auth, "authority", lambda: (MASTER_PUB, "master-id"))
    handled = []
    monkeypatch.setattr(cmt, "_handle_uncle_directive", lambda d, r: handled.append((d, r)))
    monkeypatch.setattr(socketio, "emit", lambda *a, **k: None)
    app = Flask(__name__)
    app.before_request(auth_guard.check_endpoint_auth)
    app.register_blueprint(ic_api.interconnector_bp)
    app.handled = handled
    return app


def test_an_unsigned_directive_is_refused_and_logged(directive_app, caplog):
    with caplog.at_level(logging.WARNING):
        response = _post(directive_app, DIRECTIVE, DIRECTIVE_BODY)
    assert response.status_code == 401
    assert directive_app.handled == []
    assert "Refused directive" in caplog.text


def test_the_fleet_key_alone_no_longer_sends_a_directive(directive_app):
    response = directive_app.test_client().post(
        DIRECTIVE, data=DIRECTIVE_BODY,
        headers={"Content-Type": "application/json", "X-API-Key": "fleet-key"}, environ_base=REMOTE)
    assert response.status_code == 401
    assert directive_app.handled == []


def test_a_directive_signed_by_a_client_is_refused_and_logged(directive_app, caplog):
    with caplog.at_level(logging.WARNING):
        response = _post(directive_app, DIRECTIVE, DIRECTIVE_BODY, _client_signer())
    assert response.status_code == 403
    assert directive_app.handled == []
    assert "Refused directive" in caplog.text and "master" in caplog.text


def test_a_client_cannot_borrow_the_masters_id(directive_app):
    key, _node, cert = _client_signer()
    # The master's certificate, signed with the client's key.
    response = _post(directive_app, DIRECTIVE, DIRECTIVE_BODY, (key, "master-id", MASTER_CERT))
    assert response.status_code == 401
    assert directive_app.handled == []


def test_a_directive_signed_by_the_master_is_taken_and_logged(directive_app, caplog):
    with caplog.at_level(logging.CRITICAL):
        response = _post(directive_app, DIRECTIVE, DIRECTIVE_BODY, (MASTER, "master-id", MASTER_CERT))
    assert response.status_code == 200
    assert directive_app.handled == [("lock_codebase", "test")]
    assert "lock_codebase" in caplog.text


def test_a_replayed_directive_is_refused(directive_app):
    headers = {"Content-Type": "application/json",
               **node_auth.sign_request(MASTER, "master-id", MASTER_CERT, "POST", DIRECTIVE, DIRECTIVE_BODY)}
    client = directive_app.test_client()
    assert client.post(DIRECTIVE, data=DIRECTIVE_BODY, headers=headers, environ_base=REMOTE).status_code == 200
    assert client.post(DIRECTIVE, data=DIRECTIVE_BODY, headers=headers, environ_base=REMOTE).status_code == 401
    assert len(directive_app.handled) == 1


def test_a_machine_that_never_enrolled_takes_no_directive(directive_app, monkeypatch):
    monkeypatch.setattr(node_auth, "authority", lambda: (None, None))
    response = _post(directive_app, DIRECTIVE, DIRECTIVE_BODY, (MASTER, "master-id", MASTER_CERT))
    assert response.status_code == 401
    assert directive_app.handled == []


# ---- sending directives -------------------------------------------------------

@pytest.fixture
def fleet_db(monkeypatch):
    from backend.models import InterconnectorNode, db

    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    monkeypatch.setenv("CLUSTER_NODE_ID", "master-id")
    with app.app_context():
        db.create_all()
        for node_id in ("master-id", "client-a", "client-b"):
            db.session.add(InterconnectorNode(node_id=node_id, node_name=node_id, node_mode="client",
                                              host="192.0.2.30", port=5002, status="active"))
        db.session.commit()
        yield db
        db.session.remove()
        db.drop_all()


def test_only_the_master_sends_directives(fleet_db, monkeypatch):
    from unittest.mock import patch
    from backend.services.interconnector_sync_service import InterconnectorSyncService

    monkeypatch.setattr(node_auth, "is_master", lambda: False)
    with patch("requests.post") as post:
        InterconnectorSyncService().broadcast_directive("halt_family", "test")
    post.assert_not_called()


def test_the_master_signs_each_directive_and_records_it(fleet_db, monkeypatch):
    from unittest.mock import MagicMock, patch
    from backend.models import InterconnectorBroadcast, InterconnectorBroadcastTarget
    from backend.services.interconnector_sync_service import InterconnectorSyncService

    signer = object()
    monkeypatch.setattr(node_auth, "is_master", lambda: True)
    monkeypatch.setattr(node_auth, "request_auth", lambda: signer)
    with patch("requests.post", return_value=MagicMock(ok=True, status_code=200)) as post:
        InterconnectorSyncService().broadcast_directive("halt_family", "test")
    urls = sorted(call.args[0] for call in post.call_args_list)
    assert urls == ["http://192.0.2.30:5002/api/interconnector/receive-directive"] * 2
    assert all(call.kwargs["auth"] is signer for call in post.call_args_list)
    assert all("X-API-Key" not in (call.kwargs.get("headers") or {}) for call in post.call_args_list)
    broadcast = InterconnectorBroadcast.query.one()
    assert (broadcast.sync_type, broadcast.status, broadcast.successful_count) == ("directive", "completed", 2)
    targets = {t.node_id: t.status for t in InterconnectorBroadcastTarget.query.all()}
    assert targets == {"client-a": "success", "client-b": "success"}
