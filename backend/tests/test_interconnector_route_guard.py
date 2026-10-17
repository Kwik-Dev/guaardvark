"""Every Interconnector route is accounted for: an operator route, a node
route that checks the signing machine itself, or a read left open on purpose.
Operator routes stay with this machine and the key while Network access is on.

Drives the real blueprint as a master behind the auth guard on an in-memory
database. Operator routes must refuse another host before their view runs
(so calling them here changes nothing); node routes must refuse an unsigned
caller. A route added without a place in these lists fails the first test.
/test-connection probes only this fleet's own addresses.
"""
import json
import re
from types import SimpleNamespace

import pytest
from flask import Flask

from backend.api import interconnector_api as ic_api
from backend.models import InterconnectorNode, Setting, db
from backend.utils import auth_guard, node_identity

PREFIX = "/api/interconnector"
REMOTE = {"REMOTE_ADDR": "192.0.2.40"}
LAN_DEVICE = {"REMOTE_ADDR": "192.168.1.20"}
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}

# This machine, or the install API key (backend/utils/auth_guard.py).
OPERATOR = {
    ("GET", "/config"), ("POST", "/config"), ("POST", "/config/generate-key"),
    ("GET", "/approvals/pending"), ("POST", "/approvals/<int:approval_id>/decide"),
    ("POST", "/broadcast/push"), ("GET", "/broadcast/status/<broadcast_id>"),
    ("POST", "/sync/manual"), ("POST", "/ask-family"), ("POST", "/route-inference"),
    ("POST", "/nodes/test-all"), ("GET", "/nodes"), ("DELETE", "/nodes/<node_id>"),
    ("POST", "/nodes/<node_id>/test"), ("GET", "/debug/nodes"), ("POST", "/test-connection"),
    ("POST", "/updates/apply"), ("POST", "/sync/preferences"), ("GET", "/sync/files/test"),
    ("POST", "/sync/files/verify"), ("GET", "/backups"),
    ("POST", "/enrol"), ("GET", "/enrolments"), ("POST", "/enrolments/<node_id>/approve"),
    ("POST", "/enrolments/<node_id>/revoke"), ("DELETE", "/enrolments/<node_id>"),
}
# Called by other machines; each checks the signature (backend/utils/node_auth.py).
NODE = {
    ("POST", "/nodes/register"), ("POST", "/nodes/<node_id>/heartbeat"),
    ("GET", "/sync/pull"), ("POST", "/sync/push"), ("GET", "/sync/files/pull"),
    ("POST", "/sync/files/push"), ("GET", "/updates/manifest"), ("GET", "/updates/registry"),
    ("POST", "/receive-directive"), ("GET", "/outputs/index"), ("GET", "/outputs/content"),
}
# Reads left open: a client's page checks its master's status; the master's
# connection tests read a client's sync history; the rest describe this
# machine's own sync state and change nothing.
OPEN_READS = {
    ("GET", "/status"), ("GET", "/network-info"), ("GET", "/nodes/local-identity"),
    ("GET", "/sync/history"), ("GET", "/sync/profiles"), ("GET", "/updates/check"),
    ("GET", "/updates/preview"), ("GET", "/updates/sync-status"),
    ("GET", "/sync/preferences"), ("GET", "/sync/entity-counts"),
}


def _app_rules():
    app = Flask(__name__)
    app.register_blueprint(ic_api.interconnector_bp)
    rules = set()
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith(PREFIX):
            continue
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            rules.add((method, rule.rule[len(PREFIX):]))
    return rules


def _concrete(rule):
    return PREFIX + re.sub(r"<(int:)?[^>]+>", lambda m: "7" if m.group(1) else "n-1", rule)


@pytest.fixture
def master(tmp_path, monkeypatch):
    monkeypatch.setattr(node_identity, "DEFAULT_NODE_ID_PATH", tmp_path / "node_id")
    monkeypatch.setenv(node_identity.NODE_ID_ENV, "master-node")
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv(auth_guard.API_KEY_ENV, raising=False)
    monkeypatch.delenv(auth_guard.NETWORK_ACCESS_ENV, raising=False)
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.before_request(auth_guard.check_endpoint_auth)
    app.register_blueprint(ic_api.interconnector_bp)
    with app.app_context():
        db.create_all()
        db.session.add(Setting(key="interconnector_config", value=json.dumps(
            {"is_enabled": True, "node_mode": "master", "node_name": "m", "require_api_key": False})))
        db.session.commit()
        ic_api._config_cache.update(config=None, expires=0)
        yield app
        db.session.remove()
        db.drop_all()
        ic_api._config_cache.update(config=None, expires=0)


def _set_config(app, config):
    with app.app_context():
        db.session.get(Setting, "interconnector_config").value = json.dumps(config)
        db.session.commit()
        ic_api._config_cache.update(config=None, expires=0)


def test_every_route_has_a_place():
    rules = _app_rules()
    assert OPERATOR | NODE | OPEN_READS == rules, (
        f"unlisted: {sorted(rules - OPERATOR - NODE - OPEN_READS)}; "
        f"gone: {sorted((OPERATOR | NODE | OPEN_READS) - rules)}")
    assert not (OPERATOR & NODE or OPERATOR & OPEN_READS or NODE & OPEN_READS)


@pytest.mark.parametrize("method,rule", sorted(OPERATOR))
def test_operator_routes_refuse_another_host_before_running(master, method, rule):
    response = master.test_client().open(_concrete(rule), method=method, json={}, environ_base=REMOTE)
    assert response.status_code == 403, response.get_data(as_text=True)
    assert response.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE


@pytest.mark.parametrize("method,rule", sorted(OPERATOR))
def test_operator_routes_ask_another_host_for_the_install_key(master, monkeypatch, method, rule):
    monkeypatch.setenv(auth_guard.API_KEY_ENV, "install-key")
    response = master.test_client().open(_concrete(rule), method=method, json={}, environ_base=REMOTE)
    assert response.status_code == 401


@pytest.mark.parametrize("method,rule", sorted(OPERATOR))
def test_operator_routes_stay_closed_to_a_lan_device_under_network_access(master, monkeypatch, method, rule):
    monkeypatch.setenv(auth_guard.NETWORK_ACCESS_ENV, "1")
    response = master.test_client().open(_concrete(rule), method=method, json={}, environ_base=LAN_DEVICE)
    assert response.status_code == 403, response.get_data(as_text=True)
    assert response.get_json()["code"] == auth_guard.FLEET_CODE


@pytest.mark.parametrize("method,rule", sorted(NODE - {("POST", "/sync/files/push")}))
def test_node_routes_refuse_an_unsigned_caller(master, method, rule):
    response = master.test_client().open(_concrete(rule), method=method, json={}, environ_base=REMOTE)
    assert response.status_code == 401, response.get_data(as_text=True)


def test_pushing_files_to_the_master_is_refused_outright(master):
    response = master.test_client().post(f"{PREFIX}/sync/files/push", json={}, environ_base=REMOTE)
    assert response.status_code == 400


def test_the_operator_still_reaches_the_node_list(master):
    response = master.test_client().get(f"{PREFIX}/nodes", environ_base=LOCAL)
    assert response.status_code == 200
    assert response.get_json()["data"]["nodes"] == []


# ---- /test-connection ---------------------------------------------------------

@pytest.fixture
def probes(monkeypatch):
    seen = []

    def fake_get(url, headers=None, timeout=None, verify=None):
        seen.append(url)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(ic_api.requests, "get", fake_get)
    return seen


def _test_connection(app, url, environ=LOCAL):
    return app.test_client().post(f"{PREFIX}/test-connection", json={"master_url": url}, environ_base=environ)


def test_a_client_tests_its_saved_master(master, probes):
    _set_config(master, {"is_enabled": True, "node_mode": "client", "master_url": "http://192.0.2.10:5000"})
    assert _test_connection(master, "192.0.2.10:5000").status_code == 200
    assert probes == ["http://192.0.2.10:5000/api/interconnector/status"]


def test_the_probe_url_is_rebuilt_from_the_checked_address(master, probes):
    _set_config(master, {"is_enabled": True, "node_mode": "client", "master_url": "http://192.0.2.10:5000"})
    assert _test_connection(master, "http://user:pw@192.0.2.10:5000/elsewhere?x=1").status_code == 200
    assert probes == ["http://192.0.2.10:5000/api/interconnector/status"]


def test_the_master_tests_a_node_it_knows(master, probes):
    with master.app_context():
        db.session.add(InterconnectorNode(node_id="n-2", node_name="n-2", node_mode="client",
                                          host="192.0.2.20", port=5002))
        db.session.commit()
    assert _test_connection(master, "http://192.0.2.20:5002").status_code == 200
    assert probes == ["http://192.0.2.20:5002/api/interconnector/status"]


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data",
    "http://127.0.0.1:6379",
    "192.0.2.10:5001",
    "http://192.0.2.99:5000",
    "not a url at all",
])
def test_any_other_address_is_not_fetched(master, probes, url):
    _set_config(master, {"is_enabled": True, "node_mode": "client", "master_url": "http://192.0.2.10:5000"})
    response = _test_connection(master, url)
    assert response.status_code == 400
    assert probes == []


def test_another_host_cannot_probe_even_the_master(master, probes):
    _set_config(master, {"is_enabled": True, "node_mode": "client", "master_url": "http://192.0.2.10:5000"})
    assert _test_connection(master, "192.0.2.10:5000", environ=REMOTE).status_code == 403
    assert probes == []
