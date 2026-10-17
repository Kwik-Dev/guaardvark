"""Clients that joined before enrolment keep syncing through the transition window.

Drives the real Interconnector blueprint as an updated master, behind the auth
guard, on an in-memory database. A node row with no enrolment is a client
that joined the old way; while the window is open its unsigned requests from
the address on record are taken on the fleet key, as before. Anything else
unsigned is refused as it is outside the window: another address, a machine
that has enrolled since, or any request once the window has closed.
"""
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask

from backend.api import interconnector_api as ic_api
from backend.models import InterconnectorNode, InterconnectorNodeIdentity, Setting, db
from backend.services import interconnector_transition as transition
from backend.utils import auth_guard, node_auth, node_identity

OLD_CLIENT = "old-client"
OLD_ADDRESS = "192.0.2.50"
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
PULL = "/api/interconnector/sync/pull"


def _clear_config_cache():
    ic_api._config_cache["config"] = None
    ic_api._config_cache["expires"] = 0


def _set_config(**changes):
    config = {"is_enabled": True, "node_mode": "master", "node_name": "m",
              "require_api_key": False, "sync_entities": ["rules"]}
    config.update(changes)
    db.session.get(Setting, "interconnector_config").value = json.dumps(config)
    db.session.commit()
    _clear_config_cache()


@pytest.fixture
def master(tmp_path, monkeypatch):
    monkeypatch.setattr(node_identity, "DEFAULT_NODE_ID_PATH", tmp_path / "master" / "node_id")
    monkeypatch.setenv(node_identity.NODE_ID_ENV, "master-node")
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv(auth_guard.API_KEY_ENV, raising=False)
    monkeypatch.delenv(auth_guard.NETWORK_ACCESS_ENV, raising=False)
    monkeypatch.delenv("CLUSTER_ROLE", raising=False)
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.before_request(auth_guard.check_endpoint_auth)
    app.register_blueprint(ic_api.interconnector_bp)
    with app.app_context():
        db.create_all()
        db.session.add(Setting(key="interconnector_config", value="{}"))
        # Registered by the old protocol: a node row and no enrolment.
        db.session.add(InterconnectorNode(node_id=OLD_CLIENT, node_name="old", node_mode="client",
                                          host=OLD_ADDRESS, port=5000, status="active"))
        db.session.commit()
        _set_config()
        yield app
        db.session.remove()
        db.drop_all()
        _clear_config_cache()


def _from(address):
    return {"REMOTE_ADDR": address}


def _pull(client, address=OLD_ADDRESS, headers=None):
    return client.get(PULL, query_string="entities=rules", headers=headers or {}, environ_base=_from(address))


def _heartbeat(client, node_id=OLD_CLIENT, address=OLD_ADDRESS, headers=None):
    return client.post(f"/api/interconnector/nodes/{node_id}/heartbeat", json={},
                       headers=headers or {}, environ_base=_from(address))


def _state(app):
    with app.app_context():
        return transition.read_state()


def test_the_first_old_call_opens_the_window_and_is_taken(master):
    client = master.test_client()
    assert _state(master) == {}

    response = _pull(client)

    assert response.status_code == 200, response.get_data(as_text=True)
    state = _state(master)
    assert state["until"] - state["opened_at"] == pytest.approx(transition.WINDOW_HOURS * 3600)
    assert not state["closed_at"]


def test_an_old_heartbeat_keeps_the_client_alive(master):
    response = _heartbeat(master.test_client())
    assert response.status_code == 200, response.get_data(as_text=True)
    assert "certificate" not in response.get_json()["data"]
    with master.app_context():
        assert db.session.get(InterconnectorNode, OLD_CLIENT).last_heartbeat is not None


def test_another_address_is_not_taken_and_opens_nothing(master):
    response = _pull(master.test_client(), address="192.0.2.99")
    assert response.status_code == 401
    assert _state(master) == {}


def test_a_heartbeat_naming_the_client_moves_its_address(master):
    client = master.test_client()
    assert _heartbeat(client, address="192.0.2.51").status_code == 200
    with master.app_context():
        assert db.session.get(InterconnectorNode, OLD_CLIENT).host == "192.0.2.51"
    # Pulls follow it to the new address, and no longer come from the old one.
    assert _pull(client, address="192.0.2.51").status_code == 200
    assert _pull(client, address=OLD_ADDRESS).status_code == 401


def test_a_browser_heartbeat_keeps_the_client_alive_without_moving_it(master):
    client = master.test_client()
    response = _heartbeat(client, address="192.0.2.77", headers={"Origin": "http://192.0.2.50:5173"})
    assert response.status_code == 200
    with master.app_context():
        assert db.session.get(InterconnectorNode, OLD_CLIENT).host == OLD_ADDRESS
    assert _pull(client).status_code == 200
    assert _pull(client, address="192.0.2.77").status_code == 401


def test_a_closed_window_refuses_and_does_not_reopen(master):
    client = master.test_client()
    with master.app_context():
        transition.open_window(1, now=time.time() - 7200)
    assert _pull(client).status_code == 401
    assert _heartbeat(client).status_code == 401
    assert _state(master)["until"] < time.time()


def test_the_operator_closes_and_reopens_the_window(master):
    client = master.test_client()
    assert _pull(client).status_code == 200

    closed = client.post("/api/interconnector/transition", json={"open": False}, environ_base=LOCAL)
    assert closed.status_code == 200
    assert closed.get_json()["data"]["transition"]["open"] is False
    assert _pull(client).status_code == 401

    reopened = client.post("/api/interconnector/transition", json={"open": True, "hours": 2},
                           environ_base=LOCAL)
    assert reopened.status_code == 200
    assert reopened.get_json()["data"]["transition"]["open"] is True
    assert _pull(client).status_code == 200


@pytest.mark.parametrize("body", [{}, {"open": "yes"}, {"open": True, "hours": 0},
                                  {"open": True, "hours": transition.MAX_WINDOW_HOURS + 1},
                                  {"open": True, "hours": "soon"}])
def test_the_window_is_changed_only_as_asked(master, body):
    response = master.test_client().post("/api/interconnector/transition", json=body, environ_base=LOCAL)
    assert response.status_code == 422
    assert _state(master) == {}


def test_only_the_operator_changes_the_window(master):
    response = master.test_client().post("/api/interconnector/transition", json={"open": True},
                                         environ_base=_from(OLD_ADDRESS))
    assert response.status_code == 403
    assert _state(master) == {}


def test_a_client_that_has_enrolled_is_no_longer_taken_unsigned(master):
    client = master.test_client()
    assert _pull(client).status_code == 200
    with master.app_context():
        key = Ed25519PrivateKey.generate()
        db.session.add(InterconnectorNodeIdentity(node_id=OLD_CLIENT, public_key=node_auth.public_key_text(key),
                                                  status="pending"))
        db.session.commit()
    assert _pull(client).status_code == 401
    assert _heartbeat(client).status_code == 401


def test_the_fleet_key_is_still_asked_when_the_master_requires_one(master):
    with master.app_context():
        _set_config(require_api_key=True, api_key_hash=ic_api._hash_api_key("fleet-key"))
    client = master.test_client()
    assert _pull(client).status_code == 401
    assert _pull(client, headers={"X-API-Key": "wrong"}).status_code == 401
    assert _state(master) == {}
    assert _pull(client, headers={"X-API-Key": "fleet-key"}).status_code == 200


def test_the_master_lists_who_still_needs_the_window(master):
    client = master.test_client()
    _pull(client)
    response = client.get("/api/interconnector/enrolments", environ_base=LOCAL)
    data = response.get_json()["data"]["transition"]
    assert data["open"] is True
    assert [m["node_id"] for m in data["legacy_machines"]] == [OLD_CLIENT]


@pytest.mark.parametrize("stored,address,same", [
    ("192.0.2.50", "192.0.2.50", True),
    ("http://192.0.2.50:5000", "192.0.2.50", True),
    ("192.0.2.50:5000", "::ffff:192.0.2.50", True),
    ("[fe80::1]:5000", "fe80::1%eth0", True),
    ("192.0.2.50", "192.0.2.5", False),
    ("", "192.0.2.50", False),
])
def test_addresses_compare_without_scheme_or_port(stored, address, same):
    assert (transition.host_only(stored) == transition.host_only(address) != "") is same
