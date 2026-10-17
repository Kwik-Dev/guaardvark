"""Machines join the fleet with their own key, and prove it on every request.

Drives the real Interconnector blueprint as a master, behind the auth guard,
on an in-memory database. A machine enrols with its public key and a
signature by it, waits as pending until the operator approves it, then gets a
certificate; every node route checks that certificate, the signature, the
clock, the nonce, and that the node id in the URL is the signer's.
"""
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask

from backend.api import interconnector_api as ic_api
from backend.models import InterconnectorNode, InterconnectorNodeIdentity, Setting, db
from backend.utils import auth_guard, node_auth, node_identity

REGISTER = "/api/interconnector/nodes/register"
# Another machine on the LAN, and the operator at the master.
REMOTE = {"REMOTE_ADDR": "192.0.2.10"}
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
MASTER_ID = "master-node"


def _clear_config_cache():
    ic_api._config_cache["config"] = None
    ic_api._config_cache["expires"] = 0


@pytest.fixture
def master(tmp_path, monkeypatch):
    monkeypatch.setattr(node_identity, "DEFAULT_NODE_ID_PATH", tmp_path / "master" / "node_id")
    monkeypatch.setenv(node_identity.NODE_ID_ENV, MASTER_ID)
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv(auth_guard.API_KEY_ENV, raising=False)
    monkeypatch.delenv("CLUSTER_ROLE", raising=False)
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.before_request(auth_guard.check_endpoint_auth)
    app.register_blueprint(ic_api.interconnector_bp)
    with app.app_context():
        db.create_all()
        db.session.add(Setting(key="interconnector_config", value=json.dumps(
            {"is_enabled": True, "node_mode": "master", "node_name": "m",
             "require_api_key": False, "sync_entities": ["rules"]})))
        db.session.commit()
        _clear_config_cache()
        yield app
        db.session.remove()
        db.drop_all()
        _clear_config_cache()


def _master_key():
    return node_auth.load_or_create_private_key()


class Machine:
    """A client machine: its key, its node id, and what the master gave it."""

    def __init__(self, node_id, hostname=None, key=None):
        self.node_id = node_id
        self.hostname = hostname or node_id
        self.key = key or Ed25519PrivateKey.generate()
        self.cert = None

    @property
    def public_key(self):
        return node_auth.public_key_text(self.key)

    def enrol(self, client, fleet_key=None, public_key=None, sign_with=None):
        body = json.dumps({
            "node_id": self.node_id,
            "node_name": self.hostname,
            "node_mode": "client",
            "hardware_profile": {"node_id": self.node_id, "hostname": self.hostname,
                                 "arch": "x86_64", "services": {}},
            "client_port": 5002,
            "public_key": public_key or self.public_key,
        }).encode()
        headers = {"Content-Type": "application/json"}
        if fleet_key:
            headers["X-API-Key"] = fleet_key
        headers.update(node_auth.sign_request(sign_with or self.key, self.node_id, None,
                                              "POST", REGISTER, body))
        response = client.post(REGISTER, data=body, headers=headers, environ_base=REMOTE)
        data = (response.get_json() or {}).get("data") or {}
        if data.get("certificate"):
            self.cert = data["certificate"]
        return response

    def signed(self, method, path, body=b"", query="", cert=None, node_id=None):
        return node_auth.sign_request(self.key, node_id or self.node_id, cert or self.cert,
                                      method, node_auth.path_with_query(path, query), body)

    def heartbeat(self, client, url_node_id=None, cert=None, headers=None):
        path = f"/api/interconnector/nodes/{url_node_id or self.node_id}/heartbeat"
        body = b"{}"
        sent = headers or self.signed("POST", path, body, cert=cert)
        return client.post(path, data=body, headers={"Content-Type": "application/json", **sent},
                           environ_base=REMOTE)


def _approve(client, node_id, environ=LOCAL):
    return client.post(f"/api/interconnector/enrolments/{node_id}/approve", environ_base=environ)


def _joined(client, machine):
    assert machine.enrol(client).status_code == 202
    assert _approve(client, machine.node_id).status_code == 200
    response = machine.enrol(client)
    assert response.status_code == 200, response.get_data(as_text=True)
    return machine


def _identity(app, node_id):
    with app.app_context():
        return db.session.get(InterconnectorNodeIdentity, node_id)


def _node(app, node_id):
    with app.app_context():
        return db.session.get(InterconnectorNode, node_id)


# ---- enrolment ----------------------------------------------------------------

def test_a_new_machine_waits_for_approval_then_gets_a_certificate(master):
    client = master.test_client()
    machine = Machine("node-a")

    first = machine.enrol(client)
    assert first.status_code == 202
    assert first.get_json()["data"]["status"] == "pending"
    assert first.get_json()["data"]["fingerprint"] == node_auth.fingerprint(machine.public_key)
    assert "certificate" not in first.get_json()["data"]
    assert _identity(master, "node-a").status == "pending"
    # Not a node of the fleet until approved: no row, so no routing to it.
    assert _node(master, "node-a") is None
    assert machine.enrol(client).status_code == 202

    assert _approve(client, "node-a").status_code == 200
    second = machine.enrol(client)
    assert second.status_code == 200
    data = second.get_json()["data"]
    assert data["status"] == "approved"
    with master.app_context():
        master_pub = node_auth.public_key_text(_master_key())
    assert data["master_public_key"] == master_pub
    assert data["master_node_id"] == MASTER_ID
    cert = node_auth.read_certificate(data["certificate"], master_pub)
    assert (cert.node, cert.pub, cert.iss) == ("node-a", machine.public_key, MASTER_ID)
    assert _node(master, "node-a").status == "active"


def test_the_fleet_key_is_still_asked_at_enrolment(master):
    with master.app_context():
        setting = db.session.get(Setting, "interconnector_config")
        config = json.loads(setting.value)
        config.update(require_api_key=True, api_key_hash=ic_api._hash_api_key("fleet-key"))
        setting.value = json.dumps(config)
        db.session.commit()
        _clear_config_cache()
    client = master.test_client()
    machine = Machine("node-a")
    assert machine.enrol(client).status_code == 401
    assert machine.enrol(client, fleet_key="wrong").status_code == 401
    assert _identity(master, "node-a") is None
    assert machine.enrol(client, fleet_key="fleet-key").status_code == 202


def test_a_machine_without_a_node_key_is_told_to_update(master):
    client = master.test_client()
    body = json.dumps({"node_id": "old", "node_name": "old"}).encode()
    response = client.post(REGISTER, data=body, headers={"Content-Type": "application/json"},
                           environ_base=REMOTE)
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "node_key_required"
    assert _identity(master, "old") is None and _node(master, "old") is None


def test_an_enrolment_signed_by_another_key_is_refused(master):
    machine = Machine("node-a")
    response = machine.enrol(master.test_client(), sign_with=Ed25519PrivateKey.generate())
    assert response.status_code == 401
    assert _identity(master, "node-a") is None


def test_only_the_operator_approves(master):
    client = master.test_client()
    machine = Machine("node-a")
    machine.enrol(client)
    assert _approve(client, "node-a", environ=REMOTE).status_code == 403
    # A LAN device arriving through the UI's own proxy is still another machine.
    response = client.post("/api/interconnector/enrolments/node-a/approve",
                           headers={"X-Forwarded-For": "192.0.2.10"}, environ_base=LOCAL)
    assert response.status_code == 403
    assert client.get("/api/interconnector/enrolments", environ_base=REMOTE).status_code == 403
    assert _identity(master, "node-a").status == "pending"


def test_the_operator_sees_who_waits_with_fingerprints(master):
    client = master.test_client()
    Machine("node-a").enrol(client)
    _joined(client, Machine("node-b"))
    body = client.get("/api/interconnector/enrolments", environ_base=LOCAL).get_json()["data"]
    assert [row["node_id"] for row in body["enrolments"]] == ["node-a", "node-b"]
    assert body["pending_count"] == 1
    assert all(row["fingerprint"] for row in body["enrolments"])
    assert body["master_fingerprint"]


def test_another_key_cannot_take_an_enrolled_node_id(master):
    client = master.test_client()
    _joined(client, Machine("node-a", hostname="box-a"))
    impostor = Machine("node-a", hostname="box-a")
    response = impostor.enrol(client)
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "node_key_conflict"
    assert impostor.cert is None
    assert _identity(master, "node-a").status == "approved"


def test_no_machine_enrols_under_the_masters_own_id(master):
    response = Machine(MASTER_ID).enrol(master.test_client())
    assert response.status_code == 409
    assert _identity(master, MASTER_ID) is None


def test_a_waiting_request_keeps_its_first_key(master):
    client = master.test_client()
    Machine("node-a").enrol(client)
    assert Machine("node-a").enrol(client).status_code == 409


def test_a_different_machine_with_a_copied_id_gets_its_own(master):
    client = master.test_client()
    _joined(client, Machine("node-a", hostname="box-a"))
    copy = Machine("node-a", hostname="box-b")
    response = copy.enrol(client)
    assert response.status_code == 202
    assigned = response.get_json()["data"]["node_id"]
    assert assigned != "node-a"
    assert _identity(master, assigned).status == "pending"
    assert _identity(master, "node-a").status == "approved"


def test_waiting_requests_are_capped(master, monkeypatch):
    monkeypatch.setattr(ic_api, "MAX_PENDING_ENROLMENTS", 1)
    client = master.test_client()
    assert Machine("node-a").enrol(client).status_code == 202
    response = Machine("node-b").enrol(client)
    assert response.status_code == 429
    assert _identity(master, "node-b") is None


def test_a_removed_machine_may_ask_again(master):
    client = master.test_client()
    machine = _joined(client, Machine("node-a"))
    assert client.delete("/api/interconnector/enrolments/node-a", environ_base=LOCAL).status_code == 200
    assert _identity(master, "node-a") is None and _node(master, "node-a") is None
    assert machine.heartbeat(client).status_code == 403
    assert machine.enrol(client).status_code == 202


# ---- signed node requests -----------------------------------------------------

def test_an_unsigned_heartbeat_is_refused(master):
    client = master.test_client()
    _joined(client, Machine("node-a"))
    response = client.post("/api/interconnector/nodes/node-a/heartbeat", json={}, environ_base=REMOTE)
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "node_signature_required"


def test_the_fleet_key_alone_no_longer_passes(master):
    client = master.test_client()
    _joined(client, Machine("node-a"))
    response = client.post("/api/interconnector/nodes/node-a/heartbeat", json={},
                           headers={"X-API-Key": "anything"}, environ_base=REMOTE)
    assert response.status_code == 401


def test_the_machine_itself_may_heartbeat(master):
    client = master.test_client()
    machine = _joined(client, Machine("node-a"))
    response = machine.heartbeat(client)
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["data"]["node_id"] == "node-a"
    # A fresh certificate is not renewed on every beat.
    assert "certificate" not in response.get_json()["data"]


def test_b_cannot_heartbeat_as_a(master):
    client = master.test_client()
    _joined(client, Machine("node-a"))
    b = _joined(client, Machine("node-b"))
    before = _node(master, "node-a").last_heartbeat
    response = b.heartbeat(client, url_node_id="node-a")
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "node_mismatch"
    assert _node(master, "node-a").last_heartbeat == before


def test_b_cannot_present_as_a_with_its_own_certificate(master):
    client = master.test_client()
    a = _joined(client, Machine("node-a"))
    b = _joined(client, Machine("node-b"))
    path = "/api/interconnector/nodes/node-a/heartbeat"
    # B's key, B's certificate, A's id in the header.
    headers = b.signed("POST", path, b"{}", node_id="node-a")
    assert b.heartbeat(client, url_node_id="node-a", headers=headers).status_code == 401
    # A's certificate with B's key.
    headers = b.signed("POST", path, b"{}", cert=a.cert, node_id="node-a")
    assert b.heartbeat(client, url_node_id="node-a", headers=headers).status_code == 401


def test_a_replayed_heartbeat_is_refused(master):
    client = master.test_client()
    machine = _joined(client, Machine("node-a"))
    headers = machine.signed("POST", "/api/interconnector/nodes/node-a/heartbeat", b"{}")
    assert machine.heartbeat(client, headers=headers).status_code == 200
    assert machine.heartbeat(client, headers=headers).status_code == 401


def test_a_stale_signature_is_refused(master):
    client = master.test_client()
    machine = _joined(client, Machine("node-a"))
    path = "/api/interconnector/nodes/node-a/heartbeat"
    headers = node_auth.sign_request(machine.key, "node-a", machine.cert, "POST", path, b"{}",
                                     now=int(time.time()) - 120)
    assert machine.heartbeat(client, headers=headers).status_code == 401


def test_a_certificate_from_another_master_is_refused(master):
    client = master.test_client()
    machine = _joined(client, Machine("node-a"))
    foreign = node_auth.issue_certificate(Ed25519PrivateKey.generate(), "node-a",
                                          machine.public_key, issuer="elsewhere")
    assert machine.heartbeat(client, cert=foreign).status_code == 401


def test_an_expired_certificate_is_refused_and_enrolment_renews_it(master):
    client = master.test_client()
    machine = _joined(client, Machine("node-a"))
    with master.app_context():
        expired = node_auth.issue_certificate(_master_key(), "node-a", machine.public_key, MASTER_ID,
                                              now=int(time.time()) - 2 * node_auth.CERT_LIFETIME_S)
    assert machine.heartbeat(client, cert=expired).status_code == 401
    # An approved machine gets a new certificate without a new approval.
    assert machine.enrol(client).status_code == 200
    assert machine.heartbeat(client).status_code == 200


def test_the_heartbeat_renews_a_certificate_that_runs_low(master):
    client = master.test_client()
    machine = _joined(client, Machine("node-a"))
    with master.app_context():
        short = node_auth.issue_certificate(_master_key(), "node-a", machine.public_key, MASTER_ID,
                                            lifetime_s=3600)
    response = machine.heartbeat(client, cert=short)
    assert response.status_code == 200
    renewed = response.get_json()["data"]["certificate"]
    with master.app_context():
        read = node_auth.read_certificate(renewed, node_auth.public_key_text(_master_key()))
    assert read.exp - int(time.time()) > node_auth.CERT_LIFETIME_S - 60
    # The renewed certificate keeps working.
    assert machine.heartbeat(client, cert=renewed).status_code == 200


def test_a_revoked_machine_is_refused_at_once(master):
    client = master.test_client()
    machine = _joined(client, Machine("node-a"))
    assert client.post("/api/interconnector/enrolments/node-a/revoke", environ_base=LOCAL).status_code == 200
    response = machine.heartbeat(client)
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "node_not_approved"
    enrol = machine.enrol(client)
    assert enrol.status_code == 403 and enrol.get_json()["error"]["code"] == "node_revoked"
    node = _node(master, "node-a")
    assert node.online is False and node.last_heartbeat is None


def test_pulling_data_needs_an_approved_machine(master):
    client = master.test_client()
    machine = _joined(client, Machine("node-a"))
    path = "/api/interconnector/sync/pull"
    assert client.get(path, query_string="entities=rules", environ_base=REMOTE).status_code == 401
    headers = machine.signed("GET", path, query="entities=rules")
    response = client.get(path, query_string="entities=rules", headers=headers, environ_base=REMOTE)
    assert response.status_code == 200, response.get_data(as_text=True)
    # Signed for another query: refused.
    headers = machine.signed("GET", path, query="entities=clients")
    assert client.get(path, query_string="entities=rules", headers=headers,
                      environ_base=REMOTE).status_code == 401


def test_a_waiting_machine_cannot_pull_files(master):
    client = master.test_client()
    waiting = Machine("node-a")
    waiting.enrol(client)
    with master.app_context():
        # Even a certificate this master signed is refused while not approved.
        waiting.cert = node_auth.issue_certificate(_master_key(), "node-a", waiting.public_key, MASTER_ID)
    path = "/api/interconnector/sync/files/pull"
    headers = waiting.signed("GET", path, query="credentials_only=1")
    response = client.get(path, query_string="credentials_only=1", headers=headers, environ_base=REMOTE)
    assert response.status_code == 403
    assert "portable_env" not in response.get_data(as_text=True)


def test_outputs_answer_the_operator_or_an_approved_machine(master, tmp_path, monkeypatch):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "a.png").write_bytes(b"png")
    monkeypatch.setattr(ic_api, "_get_outputs_root", lambda: outputs)
    client = master.test_client()
    machine = _joined(client, Machine("node-a"))
    path = "/api/interconnector/outputs/index"
    assert client.get(path, environ_base=REMOTE).status_code == 401
    assert client.get(path, environ_base=LOCAL).status_code == 200
    assert client.get(path, headers=machine.signed("GET", path), environ_base=REMOTE).status_code == 200


def test_a_push_is_recorded_as_the_signer(master, monkeypatch):
    seen = []

    class _Sync:
        supported_entities = {"rules": object()}

        def apply_entity(self, entity_type, data, strategy, node_id, push_mode=False):
            seen.append(node_id)
            return True, None, {"created": True}

    monkeypatch.setattr(ic_api, "get_sync_service", lambda: _Sync())
    client = master.test_client()
    machine = _joined(client, Machine("node-a"))
    path = "/api/interconnector/sync/push"
    body = json.dumps({"entities": {"rules": [{"id": 1}]}, "node_id": "someone-else"}).encode()
    response = client.post(path, data=body, headers={"Content-Type": "application/json",
                                                     **machine.signed("POST", path, body)},
                           environ_base=REMOTE)
    assert response.status_code == 200
    assert seen == ["node-a"]
