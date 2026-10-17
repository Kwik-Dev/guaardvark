"""What a client takes from its master only when the master signed it.

A broadcast from the master starts /api/interconnector/sync/manual on each
client. That route answers only this machine or the API key (auth_guard), plus
a request signed with the master's own key, the one pinned at enrolment
(backend/utils/node_auth.py). Another enrolled machine's signature is not the
master's.
"""
import json

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
